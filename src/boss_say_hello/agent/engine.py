import asyncio
from dataclasses import asdict
from itertools import count

from boss_say_hello.agent.contracts import EligibilityBlocked, validate_agent_goal
from boss_say_hello.ai.schemas import AiFailure, AiRequestInterrupted
from boss_say_hello.browser.port import ActionBlocked, BrowserFailure
from boss_say_hello.browser.preferences import compact, preferences_match
from boss_say_hello.diagnostics import failure, record, scope
from boss_say_hello.matching.rules import check_job
from boss_say_hello.runs.control import wait_until_ready

CORRECTABLE_MODEL_ERRORS = {
    "invalid_json",
    "invalid_fields",
    "invalid_score",
    "invalid_background",
    "unknown_evidence",
    "empty_content",
    "greeting_too_long",
}


class AgentEngine:
    def __init__(self, executor, ai):
        self.executor, self.ai = executor, ai
        for client in (ai, executor.ai, executor.search):
            recovery = getattr(client, "recovery", None)
            if recovery is not None:
                recovery.bind(executor.control, executor.clock, self.waiting_for_limit, self.retrying_request)

    def waiting_for_limit(self, stage, seconds, retry):
        reason = f"模型服务临时限流，等待 {seconds:g} 秒后第 {retry}/2 次重试；可暂停或结束本轮"
        self.executor.publish("state", status="running", phase="rate_limit_wait", reason=reason)

    def retrying_request(self, stage, retry):
        reason = f"已发起第 {retry}/2 次重试，正在等待模型服务响应"
        self.executor.publish("state", status="running", phase="retry_wait", reason=reason)

    async def run(self):
        with scope(run_id=self.executor.run.id):
            return await self._run()

    def next_action(self, jid):
        ex = self.executor
        if jid not in ex.details:
            return "read_job", "程序直接读取当前岗位详情并检查硬条件"
        check = check_job(ex.run.config, ex.jobs[jid])
        if check.status != "pass":
            return "skip_job", "岗位硬条件未通过：" + "；".join(check.reasons)
        if ex.run.config.auto_send:
            quota_reason = ex.journal.company_limit_reason(ex.run, ex.jobs[jid])
            if quota_reason:
                record("company.quota.blocked", job_id=jid, reason=quota_reason)
                return "skip_job", quota_reason
        if jid not in ex.company_checked:
            return "search_company", "程序读取公司资料缓存，必要时执行一次综合查询"
        if not ex.sources.get(jid):
            return "skip_job", "公司背景证据不足，本岗位待确认，不生成或发送招呼语"
        if jid not in ex.backgrounds:
            return "assess_job", "一次综合评估岗位匹配、公司背景与外包风险"
        blocked = ex.greeting_block(jid)
        if blocked:
            return "skip_job", blocked["reason"]
        if jid not in ex.greetings:
            return "draft_greeting", "评分与背景核验通过，直接生成招呼语"
        if ex.run.config.auto_send:
            return "send_greeting", "程序核验发送条件与间隔，执行沟通并检查回执"
        return "skip_job", "自动发送关闭，草稿已保存"

    async def prepare_candidates(self):
        ex = self.executor
        observed = await ex.execute(
            "inspect_preferences", {"reason": "程序直接读取已有求职标签，无需 AI 决策"}, actor="program"
        )
        if not preferences_match(observed, ex.run.config):
            target = compact(f"{ex.run.config.target_title}({ex.run.config.cities[0]})")
            options = [c for c in observed.get("controls", []) if compact(c["label"]) == target]
            if len(options) != 1:
                raise BrowserFailure("页面未找到唯一的目标求职标签，请刷新标签或在 BOSS 上核对")
            observed = await ex.execute(
                "preference_action",
                {"reason": "程序直接选择配置中的求职标签", "option_id": options[0]["id"]},
                actor="program",
            )
            if not preferences_match(observed, ex.run.config):
                raise BrowserFailure("目标求职标签未选中，请核对页面；未调用模型")
        await ex.execute("verify_preferences", {"reason": "程序核验目标标签"}, actor="program")
        if ex.candidates_need_sync:
            await ex.sync_refreshed_candidates(ex.receipt)
        if not ex.jobs and not ex.list_ended:
            await ex.execute("list_jobs", {"reason": "程序读取首批岗位，交给 AI 判断"}, actor="program")

    async def _run(self):
        ex = self.executor
        run, control = ex.run, ex.control
        validate_agent_goal(run.config)
        if not run.resume.confirmed:
            raise ValueError("请先确认简历")
        ex.store.save_run(run)
        ex.publish("started", mode="auto" if run.config.auto_send else "preview", snapshot=asdict(run))
        corrections = {}
        status = "stopped"
        empty_batches = 0
        pause_reason = ""
        try:
            for step in count():
                ex.step = step + 1
                if control.closing.is_set():
                    status = "stopped"
                    break
                if control.pause_requested.is_set():
                    ex.publish_progress()
                    ex.receipt = None
                    required = getattr(control, "default_message_required", None)
                    acknowledged = getattr(control, "default_message_acknowledged", None)
                    needs_ack = bool(
                        required and required.is_set() and acknowledged and not acknowledged.is_set()
                    )
                    ex.publish(
                        "state",
                        status="paused",
                        requires_default_ack=needs_ack,
                        reason="点击立即沟通可能先发送平台默认招呼语，然后程序会发送生成的招呼语，请阅读后点击继续本轮"
                        if needs_ack
                        else pause_reason or "任务暂停，请核对状态后继续",
                    )
                if not await wait_until_ready(control, ex.clock, ex.deadline):
                    status = "stopped"
                    break
                try:
                    await ex.guard()
                    if ex.receipt is None:
                        await self.prepare_candidates()
                    await ex.guard()
                except ActionBlocked:
                    if control.closing.is_set():
                        status = "stopped"
                        break
                    ex.publish("state", status="paused", reason="账号变化或浏览器关闭，请恢复后继续")
                    continue
                except BrowserFailure as exc:
                    if not ex.candidates_need_sync:
                        raise
                    ex.receipt = None
                    pause_reason = f"推荐列表同步未完成：{exc}；恢复页面后可继续本轮"
                    control.pause_requested.set()
                    continue
                pause_reason = ""
                if run.config.auto_send and ex.journal.success_count(run.id) >= run.config.max_contacts:
                    status = "limit_reached"
                    break
                current_job = ex.active_job_id()
                if current_job is None:
                    if ex.list_ended and not ex.candidates_need_sync:
                        status = "completed"
                        break
                    before = ex.discovered_count()
                    try:
                        await ex.execute(
                            "list_jobs",
                            {"reason": "程序继续读取更多岗位，直至成功发送目标或列表结束"},
                            actor="program",
                        )
                    except BrowserFailure as exc:
                        pause_reason = str(exc)
                        control.pause_requested.set()
                        continue
                    except ActionBlocked:
                        control.pause_requested.set()
                        continue
                    empty_batches = empty_batches + 1 if before == ex.discovered_count() else 0
                    if empty_batches >= 3 and not ex.list_ended:
                        pause_reason = "连续读取未发现新岗位，页面也未确认到底；请检查推荐列表后继续"
                        control.pause_requested.set()
                        empty_batches = 0
                    continue
                empty_batches = 0
                ex.publish_progress()
                name, reason = self.next_action(current_job)
                model_action = name in {"assess_job", "draft_greeting"}
                if model_action and not ex.begin_decision(current_job):
                    continue
                try:
                    args = {"job_id": current_job, "reason": reason}
                    if name == "assess_job":
                        ex.publish(
                            "state",
                            status="running",
                            phase="model_wait",
                            reason="正在综合评估当前岗位与公司背景",
                        )
                        with scope(step=ex.step, job_id=current_job, phase="model_wait"):
                            payload = await self.ai.evaluate_job(
                                run,
                                ex.jobs[current_job],
                                ex.companies[current_job],
                                ex.sources[current_job],
                                correction=corrections.get(current_job),
                            )
                        await ex.guard()
                        args.update(payload)
                    result = await ex.execute(name, args, actor="program")
                    if control.pause_requested.is_set() and result.get("reason"):
                        pause_reason = result["reason"]
                except AiRequestInterrupted:
                    if model_action:
                        ex.refund_decision(current_job)
                    control.pause_requested.set()
                    continue
                except ActionBlocked:
                    if model_action:
                        ex.refund_decision(current_job)
                    control.pause_requested.set()
                    continue
                except EligibilityBlocked as exc:
                    ex.done.add(current_job)
                    ex.publish(
                        "check",
                        job_id=current_job,
                        status=exc.result.get("status", "pending"),
                        reasons=[exc.result.get("reason", "当前岗位条件未满足")],
                    )
                except (ValueError, AiFailure, BrowserFailure) as exc:
                    if control.closing.is_set():
                        status = "stopped"
                        break
                    if isinstance(exc, AiFailure) and exc.is_limit:
                        if model_action:
                            ex.refund_decision(current_job)
                        pause_reason = exc.public_message + "；已保留当前岗位，处理后点击继续本轮"
                        control.pause_requested.set()
                        continue
                    reason = exc.public_message if isinstance(exc, AiFailure) else str(exc)
                    code = (
                        exc.code
                        if isinstance(exc, AiFailure)
                        else "browser_failed"
                        if isinstance(exc, BrowserFailure)
                        else "invalid_fields"
                    )
                    failure("agent.tool.failed", exc, tool=name, step=step + 1, code=code, reason=reason)
                    result = {"error": reason, "code": code}
                    if isinstance(exc, BrowserFailure):
                        pause_reason = f"页面操作暂停：{reason}"
                        control.pause_requested.set()
                    elif isinstance(exc, AiFailure) and (
                        not model_action or exc.code not in CORRECTABLE_MODEL_ERRORS
                    ):
                        if model_action:
                            ex.refund_decision(current_job)
                        pause_reason = (
                            f"模型服务请求失败：{reason}；已保留本轮与当前岗位。"
                            "网络或服务恢复后可继续；如需修改 API 地址、模型或密钥，"
                            "请结束本轮修改后重新开始"
                        )
                        control.pause_requested.set()
                        result.update(status="paused", reason=pause_reason)
                    elif model_action:
                        result = ex.model_failure_result(name, current_job, result)
                        if name == "assess_job":
                            corrections[current_job] = result
                    else:
                        ex.done.add(current_job)
                        ex.publish("check", job_id=current_job, status="pending", reasons=[reason])
                    ex.publish("tool_result", tool=name, actor="program", job_id=current_job, result=result)
                if run.config.auto_send and ex.journal.success_count(run.id) >= run.config.max_contacts:
                    status = "limit_reached"
                    break
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception as exc:
            status = "failed"
            details = exc.diagnostic_fields() if isinstance(exc, AiFailure) else {}
            failure("agent.run.failed", exc, run_id=run.id, **details)
            ex.publish(
                "error",
                reason=exc.public_message
                if isinstance(exc, AiFailure)
                else str(exc)
                if isinstance(exc, BrowserFailure)
                else "代理运行失败，已保存步骤记录；不会重试发送",
            )
        finally:
            reasons = {
                "completed": "当前推荐列表已无更多待处理岗位",
                "limit_reached": "已达到本轮成功发送目标",
                "stopped": "用户已结束本轮",
                "interrupted": "运行中断，已保存记录",
                "failed": "运行发生错误，详情见错误记录",
            }
            ex.publish(
                "finished",
                status=status,
                contacts=ex.journal.contact_count(run.id),
                successes=ex.journal.success_count(run.id),
                contact_limit=run.config.max_contacts,
                discovered=ex.discovered_count(),
                handled=len(ex.done),
                reason=reasons.get(status, status),
            )
