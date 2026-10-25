import asyncio

from boss_say_hello.browser.port import ActionBlocked, ActionResult
from boss_say_hello.diagnostics import failure
from boss_say_hello.domain import RunEvent
from boss_say_hello.greetings.service import validate_greeting
from boss_say_hello.matching.rules import check_job


class DeliveryService:
    def __init__(self, browser, journal):
        self.browser = browser
        self.journal = journal

    async def deliver(self, run, job, assessment, greeting, control):
        def event(status, **data):
            return [RunEvent(run.id, "delivery", {"job_id": job.id, "status": status, **data})]

        if not run.config.auto_send:
            return event("preview")
        run.config.validate(ready=True)
        if (
            not run.resume.confirmed
            or type(assessment.score) is not int
            or not run.config.min_score <= assessment.score <= 100
        ):
            return event("blocked", reason="简历未确认或匹配分不足")
        if control.pause_requested.is_set() or control.closing.is_set():
            return event("paused")
        session = await self.browser.session()
        if not session.logged_in or session.account_id != run.account_id or job.account_id != run.account_id:
            return event("session_changed")
        latest = await self.browser.refresh_job(job)
        if any(
            getattr(latest, k) != getattr(job, k)
            for k in (
                "id",
                "account_id",
                "title",
                "company",
                "text",
                "city",
                "salary_raw",
                "recruiter_id",
                "recruiter_name",
                "company_id",
                "company_full_name",
                "company_url",
            )
        ):
            return event("changed", reason="岗位已变化，请在下一轮重新评估")
        if check_job(run.config, latest).status != "pass":
            return event("blocked", reason="最新岗位条件不满足")
        caps = await self.browser.capabilities()
        if (
            caps.initial_contact_behavior in {"default_message", "may_default_message"}
            and not control.default_message_acknowledged.is_set()
        ):
            control.default_message_required.set()
            control.pause_requested.set()
            return event(
                "needs_default_ack",
                reason="点击立即沟通可能先发送平台默认招呼语，然后程序会发送生成的招呼语；阅读说明后点击继续本轮才会执行",
            )
        if caps.initial_contact_behavior == "unknown":
            return event("blocked", reason="平台沟通行为尚未核实，本次未发送")
        greeting_check = validate_greeting(greeting, caps.greeting_limit)
        if greeting_check.status != "pass":
            return event("blocked", reason="；".join(greeting_check.reasons))
        if control.pause_requested.is_set() or control.closing.is_set():
            return event("paused")
        self.browser.bind_control(control)
        attempt = self.journal.reserve(run, job)
        if not attempt:
            return event("skipped", reason="已联系或已达本轮上限")
        actions = [
            ("contact", lambda: self.browser.open_contact(job)),
            ("message", lambda: self.browser.send_text(job, greeting.text)),
        ]
        for stage, action in actions:
            if control.pause_requested.is_set() or control.closing.is_set():
                if not self.journal.stages(attempt):
                    self.journal.cancel_before_contact(attempt)
                return event("paused", stages=self.journal.stages(attempt))
            self.journal.begin_stage(attempt, stage)
            try:
                result = await action()
                if result.outcome not in ("succeeded", "failed", "unknown"):
                    result = ActionResult("unknown", "平台响应无法核实")
            except ActionBlocked:
                self.journal.finish_stage(attempt, stage, ActionResult("cancelled", "暂停，尚未发起此动作"))
                if stage == "contact":
                    self.journal.cancel_before_contact(attempt)
                return event("paused", stages=self.journal.stages(attempt))
            except asyncio.CancelledError:
                self.journal.finish_stage(attempt, stage, ActionResult("unknown", "请求中断，结果待核实"))
                raise
            except Exception as exc:
                failure("delivery.failed", exc, run_id=run.id, job_id=job.id, attempt_id=attempt, stage=stage)
                result = ActionResult("unknown", "操作异常，结果待核实；不会自动重发")
            if stage == "contact" and result.outcome == "failed" and result.attempted is False:
                self.journal.finish_stage(attempt, stage, ActionResult("cancelled", result.detail))
                self.journal.cancel_before_contact(attempt)
                return event(
                    "failed", stages=self.journal.stages(attempt), reason="未点击立即沟通，已释放本次预留"
                )
            self.journal.finish_stage(attempt, stage, result)
            if result.outcome != "succeeded":
                returned = (
                    await self._return_to_list(run, control)
                    if result.outcome == "failed" and stage != "contact"
                    else False
                )
                return event(result.outcome, stages=self.journal.stages(attempt), returned_to_list=returned)
        returned = await self._return_to_list(run, control)
        return event("sent", stages=self.journal.stages(attempt), returned_to_list=returned)

    async def _return_to_list(self, run, control):
        try:
            if control.pause_requested.is_set() or control.closing.is_set():
                return False
            session = await self.browser.session()
            if (
                not session.logged_in
                or session.account_id != run.account_id
                or control.pause_requested.is_set()
                or control.closing.is_set()
            ):
                return False
            return await self.browser.return_to_list()
        except Exception as exc:
            failure("browser.return_failed", exc, run_id=run.id)
            return False
