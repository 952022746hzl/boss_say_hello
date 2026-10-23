import asyncio
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.browser.port import BrowserFailure
from boss_say_hello.domain import RunEvent, public_dict
from boss_say_hello.runs.control import wait_until_ready


class RunEngine:
    def __init__(self, store, browser, matching, greetings, delivery, journal, clock, emit):
        self.store, self.browser, self.matching, self.greetings = store, browser, matching, greetings
        self.delivery, self.journal, self.clock, self.emit = delivery, journal, clock, emit

    def publish(self, event):
        self.store.append_event(event)
        self.emit(event)

    async def ready(self, run, control, deadline=0):
        while not control.closing.is_set():
            if control.pause_requested.is_set():
                self.publish(RunEvent(run.id,'state',{'status':'paused','reason':'任务暂停；已发起的步骤不重发'}))
            if not await wait_until_ready(control,self.clock,deadline):
                return False
            session = await self.browser.session()
            if session.logged_in and session.account_id == run.account_id:
                self.publish(RunEvent(run.id,'state',{'status':'running'}))
                return True
            control.pause_requested.set()
            self.publish(RunEvent(run.id,'error',{'reason':'登录失效或账号变化，请恢复原账号后继续'}))
        return False

    async def run(self, snapshot, control):
        run=snapshot
        run.config.validate(ready=True)
        if not run.resume.confirmed:
            raise ValueError('请先确认简历')
        self.store.save_run(run)
        self.publish(RunEvent(run.id,'started',{'mode':'auto' if run.config.auto_send else 'preview','snapshot':public_dict(run)}))
        deadline=0; seen=set(); status='completed'
        iterator=self.browser.iter_recommended().__aiter__()
        try:
            while await self.ready(run,control):
                if run.config.auto_send and self.journal.contact_count(run.id)>=run.config.max_contacts:
                    status='limit_reached'; break
                try:
                    job=await anext(iterator)
                except StopAsyncIteration:
                    break
                except BrowserFailure as exc:
                    self.publish(RunEvent(run.id,'error',{'reason':str(exc)}))
                    control.pause_requested.set()
                    iterator=self.browser.iter_recommended().__aiter__()
                    continue
                if job.account_id != run.account_id:
                    control.pause_requested.set()
                    continue
                if job.id and job.id in seen:
                    continue
                if job.id:
                    seen.add(job.id)
                def report(kind, **data):
                    self.publish(RunEvent(run.id,kind,{'job_id':job.id,**data}))
                report('job',job=public_dict(job))
                if not self.journal.eligible(run.account_id,job.id):
                    report('check',status='skipped',reasons=['本地记录已联系或结果未知'])
                    continue
                try:
                    check,assessment=await self.matching.evaluate(run,job)
                    report('check',**public_dict(check))
                    if assessment is None:
                        continue
                    report('assessment',**public_dict(assessment))
                    if assessment.score < run.config.min_score:
                        report('check',status='low_score',reasons=['未达到匹配分门槛'])
                        continue
                    greeting=await self.greetings.generate(run,job,assessment)
                    report('greeting',**public_dict(greeting))
                except Exception as exc:
                    report('error',reason=exc.public_message if isinstance(exc,AiFailure) else '岗位评估或招呼语生成失败，本岗位不发送')
                    continue
                if not await self.ready(run,control,deadline if run.config.auto_send else 0):
                    break
                before=self.journal.contact_count(run.id)
                events=await self.delivery.deliver(run,job,assessment,greeting,control)
                if self.journal.contact_count(run.id)>before:
                    # Starting the interval after delivery is conservative even when a page responds slowly.
                    deadline=self.clock.monotonic()+run.config.interval_seconds
                for event in events:
                    self.publish(event)
                    if event.payload['status'] in ('unknown','session_changed'):
                        control.pause_requested.set()
            if control.closing.is_set():
                status='stopped'
        except asyncio.CancelledError:
            status='interrupted'
            raise
        except Exception:
            status='failed'
            self.publish(RunEvent(run.id,'error',{'reason':'运行中断，请检查登录和本地记录；不会自动重试发送'}))
        finally:
            await iterator.aclose()
            self.publish(RunEvent(run.id,'finished',{'status':status,'contacts':self.journal.contact_count(run.id)}))
