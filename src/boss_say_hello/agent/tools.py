from dataclasses import asdict

from boss_say_hello.agent.contracts import background_decision, combined_assessment
from boss_say_hello.agent.tool_schema import DEFINITIONS, TEXT, obj, validate_value
from boss_say_hello.ai.schemas import parse_assessment
from boss_say_hello.browser.port import ActionBlocked, BrowserFailure
from boss_say_hello.domain import RunEvent
from boss_say_hello.matching.rules import check_job


class ToolExecutor:
    def __init__(self, run, browser, ai, search, cache, store, delivery, journal, control, clock, emit):
        self.run, self.browser, self.ai, self.search, self.cache = run, browser, ai, search, cache
        self.store, self.delivery, self.journal = store, delivery, journal
        self.control, self.clock, self.emit = control, clock, emit
        self.jobs, self.details, self.companies, self.sources = {}, set(), {}, {}
        self.assessments, self.backgrounds, self.greetings = {}, {}, {}
        self.job_steps, self.search_counts, self.excluded, self.done = {}, {}, set(), set()
        self.receipt, self.observation, self.deadline = None, None, 0
        self.finished = False
        browser.bind_control(control)
        browser.bind_goal(run.config)

    def publish(self, kind, **payload):
        event = RunEvent(self.run.id, kind, payload)
        self.store.append_event(event)
        self.emit(event)

    async def guard(self):
        if self.control.closing.is_set() or self.control.pause_requested.is_set():
            raise ActionBlocked("任务暂停或已停止")
        session = await self.browser.session()
        if not session.logged_in or session.account_id != self.run.account_id:
            self.control.pause_requested.set()
            raise ActionBlocked("登录失效或账号变化，请恢复原账号后继续")

    async def execute(self, name, args):
        if name not in DEFINITIONS or not isinstance(args, dict):
            raise ValueError("未知工具")
        validate_value(args, obj({"reason": TEXT, **DEFINITIONS[name][1]}))
        await self.guard()
        jid = args.get("job_id")
        if jid is not None:
            if not isinstance(jid, str) or jid not in self.jobs or jid in self.done:
                raise ValueError("岗位未读取或已完成处理")
            steps = self.job_steps.get(jid, 0) + 1
            self.job_steps[jid] = steps
            if steps > self.run.config.max_job_steps:
                self.done.add(jid)
                raise ValueError("单岗位决策步数已达到上限")
        self.publish("tool_action", tool=name, job_id=jid, reason=args["reason"][:1000])
        result = await getattr(self, name)(**{k: v for k, v in args.items() if k != "reason"})
        self.publish("tool_result", tool=name, job_id=jid, result=result)
        return result

    async def inspect_preferences(self):
        self.observation = await self.browser.preference_snapshot()
        return self.observation

    async def open_preferences(self, destination):
        if self.jobs:
            raise ValueError("读取候选后不能切换筛选页面")
        self.receipt = None
        self.observation = await self.browser.open_preferences(destination)
        return self.observation

    async def preference_action(self, action, option_id, value):
        if self.jobs:
            raise ValueError("已读取候选后不能更改条件，请开启新一轮")
        self.receipt = None
        self.observation = await self.browser.preference_action(action, option_id, value)
        return self.observation

    async def verify_preferences(self):
        if not self.observation or not await self.browser.verify_preferences(self.observation):
            self.receipt = None
            raise ValueError("平台条件与本轮配置不一致，请继续核对或停止，不可使用未筛选列表")
        self.receipt = self.observation
        self.publish("preferences", snapshot=self.receipt)
        return {"verified": True}

    async def list_jobs(self):
        if not self.receipt or not await self.browser.verify_preferences(self.receipt):
            raise ValueError("请先核实原生筛选条件")
        remaining = self.run.config.max_candidates - len(self.jobs)
        if remaining <= 0:
            return {"jobs": [], "ended": True, "reason": "候选上限"}
        batch = await self.browser.candidate_batch()
        fresh = []
        for job in batch["jobs"]:
            if job.account_id != self.run.account_id or not job.id or job.id in self.jobs:
                continue
            if len(fresh) >= remaining:
                break
            self.jobs[job.id] = job
            self.publish("job", job_id=job.id, job=asdict(job))
            if not self.journal.eligible(self.run.account_id, job.id):
                self.done.add(job.id)
                self.publish("check", job_id=job.id, status="skipped", reasons=["本地已联系或结果未知"])
                continue
            fresh.append(asdict(job))
        return {"jobs": fresh, "ended": batch["ended"] or len(self.jobs) >= self.run.config.max_candidates}

    def invalidate(self, jid):
        self.assessments.pop(jid, None)
        self.backgrounds.pop(jid, None)
        self.greetings.pop(jid, None)

    async def read_job(self, job_id):
        job = await self.browser.refresh_job(self.jobs[job_id])
        if job.id != job_id or job.account_id != self.run.account_id:
            raise BrowserFailure("岗位身份变化")
        if job != self.jobs[job_id]:
            self.invalidate(job_id)
            self.sources.pop(job_id, None)
            self.companies.pop(job_id, None)
        self.jobs[job_id] = job
        self.details.add(job_id)
        check = check_job(self.run.config, job)
        self.publish("job", job_id=job_id, job=asdict(job))
        self.publish("check", job_id=job_id, **asdict(check))
        return {"job": asdict(job), "check": asdict(check)}

    async def search_company(self, job_id, angle):
        # Revoke before any await: failed or interrupted refresh cannot leave an old send permit.
        self.invalidate(job_id)
        self.sources.pop(job_id, None)
        self.companies.pop(job_id, None)
        if angle not in {"identity", "business", "employment", "risk"}:
            raise ValueError("查询角度无效")
        company = await self.browser.company_info(self.jobs[job_id])
        if not company.get("name") or not company.get("city"):
            raise ValueError("公司名称或城市不足，需人工确认")
        identity = {k: company.get(k, "") for k in ("id", "name", "city", "url")}
        key = self.cache.key(identity)
        sources = self.cache.get(identity, self.run.config.company_cache_days) or []
        if not any(s.get("angle") == angle for s in sources):
            count = self.search_counts.get(key, 0)
            if count >= self.run.config.max_company_searches:
                raise ValueError("本轮该公司联网查询次数已达上限")
            self.search_counts[key] = count + 1
            found = await self.search.search(company, angle)
            sources = list({s["id"]: s for s in sources + found}.values())
            if not sources:
                raise ValueError("没有可引用的公司来源，保持待确认")
            self.cache.put(identity, sources)
        self.companies[job_id], self.sources[job_id] = company, sources
        self.publish("company_sources", job_id=job_id, company=company, sources=sources)
        return {"company": company, "sources": sources}

    async def assess_job(self, job_id, assessment, background):
        job = self.jobs[job_id]
        if job_id not in self.details or not self.sources.get(job_id):
            raise ValueError("请先读取岗位详情和真实公司来源")
        check = check_job(self.run.config, job)
        if check.status != "pass":
            return asdict(check)
        self.invalidate(job_id)
        bg = background_decision(background, self.sources[job_id], job, self.run.config)
        if bg.status == "excluded":
            self.excluded.add(job_id)
        self.backgrounds[job_id] = bg
        self.publish("background", job_id=job_id, **asdict(bg))
        if bg.status != "pass" or job_id in self.excluded:
            return {"status": "excluded" if job_id in self.excluded else bg.status, "reason": bg.reason}
        combined = combined_assessment(parse_assessment(assessment), bg, self.run.config)
        self.assessments[job_id] = combined
        self.publish("assessment", job_id=job_id, **asdict(combined))
        return {"status": "pass", **asdict(combined)}

    def assessed(self, jid):
        if (
            jid not in self.assessments
            or jid in self.excluded
            or self.backgrounds[jid].status != "pass"
            or self.assessments[jid].score < self.run.config.min_score
        ):
            raise ValueError("岗位未通过背景检查或匹配分门槛")

    async def draft_greeting(self, job_id):
        self.assessed(job_id)
        greeting = await self.ai.greet(self.run, self.jobs[job_id], self.assessments[job_id])
        await self.guard()
        self.greetings[job_id] = greeting
        self.publish("greeting", job_id=job_id, **asdict(greeting))
        if not self.run.config.auto_send:
            self.done.add(job_id)
        return {"mode": "auto" if self.run.config.auto_send else "preview", **asdict(greeting)}

    async def send_greeting(self, job_id):
        self.assessed(job_id)
        if job_id not in self.greetings or not self.run.config.auto_send:
            raise ValueError("未生成招呼语或自动发送关闭")
        if not self.receipt or not await self.browser.verify_preferences(self.receipt):
            raise ValueError("原生筛选已变化，本次不发送")
        if self.journal.contact_count(self.run.id) >= self.run.config.max_contacts:
            raise ValueError("本轮发送上限已达到")
        if self.clock.monotonic() < self.deadline:
            return {"status": "waiting", "seconds": round(self.deadline - self.clock.monotonic(), 1)}
        await self.guard()
        before = self.journal.contact_count(self.run.id)
        events = await self.delivery.deliver(
            self.run, self.jobs[job_id], self.assessments[job_id], self.greetings[job_id], self.control
        )
        if self.journal.contact_count(self.run.id) > before:
            self.deadline = self.clock.monotonic() + self.run.config.interval_seconds
        for event in events:
            self.store.append_event(event)
            self.emit(event)
            if event.payload["status"] in {"unknown", "session_changed", "needs_default_ack"}:
                self.control.pause_requested.set()
        if not any(e.payload["status"] in {"paused", "session_changed", "needs_default_ack"} for e in events):
            self.done.add(job_id)
        return {"events": [asdict(e) for e in events]}

    async def skip_job(self, job_id):
        self.done.add(job_id)
        self.publish("check", job_id=job_id, status="skipped", reasons=["AI 决定跳过，理由见步骤记录"])
        return {"status": "skipped"}

    async def finish(self):
        self.finished = True
        return {"status": "finished", "discovered": len(self.jobs), "handled": len(self.done)}

    def summary(self):
        return {
            "verified": bool(self.receipt),
            "jobs": [
                {
                    "id": jid,
                    "title": job.title,
                    "detail_read": jid in self.details,
                    "sources": len(self.sources.get(jid, [])),
                    "assessed": jid in self.assessments,
                    "greeting": jid in self.greetings,
                    "done": jid in self.done,
                }
                for jid, job in self.jobs.items()
            ],
        }
