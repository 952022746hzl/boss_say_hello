import time
from dataclasses import asdict

from boss_say_hello.agent.contracts import EligibilityBlocked, background_decision, combined_assessment
from boss_say_hello.agent.progress import PHASES, TOOL_LABELS
from boss_say_hello.agent.tool_schema import DEFINITIONS, TEXT, obj, validate_value
from boss_say_hello.ai.schemas import parse_assessment
from boss_say_hello.browser.port import ActionBlocked, BrowserFailure, JobUnavailable
from boss_say_hello.browser.preferences import compact
from boss_say_hello.diagnostics import record, scope
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
        self.company_checked = set()
        self.discovered_ids = set()
        self.list_ended = False
        self.candidates_need_sync = False
        self.model_failures = {}
        self.focus_job = None
        self.finished = False
        self.step = 0
        browser.bind_control(control)
        browser.bind_goal(run.config)

    def publish(self, kind, **payload):
        payload.setdefault("step", self.step)
        event = RunEvent(self.run.id, kind, payload)
        self.store.append_event(event)
        self.emit(event)

    async def guard(self):
        if self.control.closing.is_set() or self.control.pause_requested.is_set():
            raise ActionBlocked("任务暂停或已停止")
        session = await self.browser.session()
        if self.control.closing.is_set() or self.control.pause_requested.is_set():
            raise ActionBlocked("任务暂停或已停止")
        if not session.logged_in or session.account_id != self.run.account_id:
            self.control.pause_requested.set()
            raise ActionBlocked("登录失效或账号变化，请恢复原账号后继续")

    async def execute(self, name, args, *, actor="ai"):
        if name not in DEFINITIONS or not isinstance(args, dict):
            raise ValueError("未知工具")
        validate_value(args, obj({"reason": TEXT, **DEFINITIONS[name][1]}))
        await self.guard()
        jid = args.get("job_id")
        current = self.active_job_id()
        if current is not None and (name == "list_jobs" or (jid is not None and jid != current)):
            raise EligibilityBlocked(
                current,
                {
                    "status": "pending",
                    "code": "current_job_unfinished",
                    "reason": "当前岗位尚未处理完，请先完成评分、生成发送或明确跳过，再处理下一岗位",
                },
            )
        if jid is not None:
            if not isinstance(jid, str) or jid not in self.jobs or jid in self.done:
                raise ValueError("岗位未读取或已完成处理")
            self.focus_job = jid
            steps = self.job_steps.get(jid, 0) + 1
            self.job_steps[jid] = steps
            if steps > self.run.config.max_job_steps:
                self.done.add(jid)
                raise ValueError("单岗位决策步数已达到上限")
        self.publish(
            "state",
            status="running",
            phase=PHASES[name],
            tool=name,
            actor=actor,
            reason=f"第 {self.step} 步｜{TOOL_LABELS[name]}",
        )
        self.publish(
            "tool_action",
            tool=name,
            actor=actor,
            phase=PHASES[name],
            job_id=jid,
            reason=args["reason"][:1000],
        )
        started = time.monotonic()
        with scope(run_id=self.run.id, step=self.step, tool=name, job_id=jid, phase=PHASES[name]):
            try:
                result = await getattr(self, name)(**{k: v for k, v in args.items() if k != "reason"})
            except JobUnavailable:
                if jid is None:
                    raise
                await self.guard()
                if not self.receipt or not await self.browser.verify_preferences(self.receipt):
                    self.receipt = None
                    self.control.pause_requested.set()
                    raise BrowserFailure("岗位消失且求职标签无法核验，请恢复目标标签后继续") from None
                result = self.skip_unavailable(jid)
            record(
                "agent.tool.completed",
                level="DEBUG",
                duration_ms=round((time.monotonic() - started) * 1000),
                result_keys=list(result) if isinstance(result, dict) else [],
            )
        self.publish("tool_result", tool=name, actor=actor, job_id=jid, result=result)
        return result

    async def inspect_preferences(self):
        self.observation = await self.browser.preference_snapshot()
        return self.observation

    async def open_preferences(self):
        if self.jobs:
            raise ValueError("读取候选后不能切换筛选页面")
        self.receipt = None
        self.observation = await self.browser.open_preferences()
        return self.observation

    async def preference_action(self, option_id):
        self.receipt = None
        option = next((c for c in (self.observation or {}).get("controls", []) if c["id"] == option_id), None)
        if self.jobs:
            target = compact(f"{self.run.config.target_title}({self.run.config.cities[0]})")
            if not option or compact(option["label"]) != target:
                raise ValueError("读取候选后只能恢复本轮已配置标签，不能切换目标")
        if option:
            action = "点击"
            self.publish(
                "state",
                status="running",
                phase="browser_action",
                reason=f"第 {self.step} 步｜操作页面：{action}「{option['label']}」",
            )
        self.observation = await self.browser.preference_action(option_id)
        self.publish(
            "state",
            status="running",
            phase="verify",
            reason=f"第 {self.step} 步｜已回读标签状态，核对操作结果",
        )
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
            self.receipt = None
            self.control.pause_requested.set()
            raise BrowserFailure("求职标签已变化，已暂停；继续本轮时将重新核验配置中的标签")
        self.discovered_ids.update(self.jobs)
        remaining = self.run.config.max_candidates - self.discovered_count()
        if remaining <= 0:
            self.list_ended = True
            return {"jobs": [], "ended": True, "reason": "候选上限"}
        batch = await self.browser.candidate_batch()
        fresh = []
        for job in batch["jobs"]:
            if job.account_id != self.run.account_id or not job.id or job.id in self.discovered_ids:
                continue
            if self.discovered_count() >= self.run.config.max_candidates:
                break
            self.jobs[job.id] = job
            self.discovered_ids.add(job.id)
            self.publish("job", job_id=job.id, job=asdict(job))
            if not self.journal.eligible(self.run.account_id, job.id):
                self.done.add(job.id)
                self.publish("check", job_id=job.id, status="skipped", reasons=["本地已联系或结果未知"])
                continue
            fresh.append(asdict(job))
        self.list_ended = batch["ended"] or self.discovered_count() >= self.run.config.max_candidates
        return {"jobs": fresh, "ended": self.list_ended}

    def active_job_id(self):
        if self.focus_job in self.jobs and self.focus_job not in self.done:
            return self.focus_job
        return next((jid for jid in self.jobs if jid not in self.done), None)

    def discovered_count(self):
        return len(self.discovered_ids | self.jobs.keys())

    def source_ids(self, jid):
        return sorted({s["id"] for s in self.sources.get(jid, []) if s.get("text") and s.get("url")})

    def model_failure_result(self, name, jid, result):
        if (
            jid is None
            or jid in self.done
            or jid != self.active_job_id()
            or name not in {"assess_job", "draft_greeting"}
        ):
            return result
        key = (jid, name)
        count = self.model_failures.get(key, 0) + 1
        self.model_failures[key] = count
        result = {**result, "job_id": jid, "corrections_remaining": max(0, 2 - count)}
        if result["code"] == "unknown_evidence":
            result["allowed_source_ids"] = self.source_ids(jid)
        if count >= 2:
            reason = f"{TOOL_LABELS[name]}纠正一次后仍失败：{result['error']}；本岗位标记待确认并跳过，不发送"
            self.invalidate(jid)
            self.done.add(jid)
            result.update(status="pending", reason=reason)
            self.publish("check", job_id=jid, status="pending", reasons=[reason])
        else:
            result.update(
                status="correction_required", reason="请按工具参数规则纠正一次；仍失败则本岗位待确认并跳过"
            )
        record(
            "agent.model.correction",
            level="WARNING",
            tool=name,
            job_id=jid,
            count=count,
            code=result["code"],
            reason=result["reason"],
        )
        return result

    def discard_job_context(self, jid):
        self.discovered_ids.add(jid)
        self.invalidate(jid)
        self.jobs.pop(jid, None)
        self.companies.pop(jid, None)
        self.sources.pop(jid, None)
        self.details.discard(jid)
        self.company_checked.discard(jid)
        self.job_steps.pop(jid, None)
        if self.focus_job == jid:
            self.focus_job = None

    def invalidate(self, jid):
        self.assessments.pop(jid, None)
        self.backgrounds.pop(jid, None)
        self.greetings.pop(jid, None)

    def skip_unavailable(self, jid):
        self.invalidate(jid)
        self.done.add(jid)
        result = {
            "status": "skipped",
            "code": "job_unavailable",
            "reason": "岗位已不在当前推荐列表，本轮跳过并继续处理其他岗位",
        }
        self.publish("check", job_id=jid, status="skipped", reasons=[result["reason"]])
        record("agent.job.unavailable", level="WARNING", job_id=jid, **result)
        return result

    async def sync_refreshed_candidates(self, observed, sending_job_id=None):
        visible_ids = await self.browser.current_candidate_ids()
        await self.guard()
        if not await self.browser.verify_preferences(observed):
            raise BrowserFailure("同步刷新列表时求职标签变化，请恢复目标标签后继续")
        stale = [
            jid
            for jid in self.jobs
            if jid not in self.done and jid != sending_job_id and jid not in visible_ids
        ]
        for jid in stale:
            self.skip_unavailable(jid)
        for jid in list(self.jobs):
            if jid in self.done or jid == sending_job_id:
                self.discard_job_context(jid)
        self.list_ended = self.discovered_count() >= self.run.config.max_candidates
        self.candidates_need_sync = False
        record("agent.candidates.synced", count=len(stale))

    async def read_job(self, job_id):
        job = await self.browser.refresh_job(self.jobs[job_id])
        if job.id != job_id or job.account_id != self.run.account_id:
            raise BrowserFailure("岗位身份变化")
        if job != self.jobs[job_id]:
            self.invalidate(job_id)
            self.sources.pop(job_id, None)
            self.companies.pop(job_id, None)
            self.company_checked.discard(job_id)
        self.jobs[job_id] = job
        self.details.add(job_id)
        check = check_job(self.run.config, job)
        self.publish("job", job_id=job_id, job=asdict(job))
        self.publish("check", job_id=job_id, **asdict(check))
        return {"job": asdict(job), "check": asdict(check)}

    async def search_company(self, job_id):
        if job_id in self.company_checked:
            return {
                "company": self.companies.get(job_id, {}),
                "sources": self.sources.get(job_id, []),
                "status": "available" if self.sources.get(job_id) else "pending",
                "reason": "本轮已完成综合查询，复用结果；证据不足保持待确认",
            }
        self.invalidate(job_id)
        self.sources.pop(job_id, None)
        self.companies.pop(job_id, None)
        company = await self.browser.company_info(self.jobs[job_id])
        if not company.get("name") or not company.get("city"):
            raise ValueError("公司名称或城市不足，需人工确认")
        self.company_checked.add(job_id)
        self.companies[job_id] = company
        identity = {k: company.get(k, "") for k in ("id", "name", "city", "url")}
        key = self.cache.key(identity)
        sources = [
            s
            for s in self.cache.get(identity, self.run.config.company_cache_days) or []
            if s.get("angle") == "overview"
        ]
        if not sources and self.search_counts.get(key, 0) < 1:
            self.search_counts[key] = 1
            found = await self.search.search(company)
            sources = [{**source, "angle": "overview"} for source in found]
            if sources:
                self.cache.put(identity, sources)
                sources = self.cache.get(identity, self.run.config.company_cache_days) or []
        self.sources[job_id] = sources
        self.publish("company_sources", job_id=job_id, company=company, sources=sources)
        return {
            "company": company,
            "sources": sources,
            "status": "available" if sources else "pending",
            "reason": "综合查询完成；证据不足保持待确认，不自动追加查询",
        }

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

    def greeting_block(self, jid):
        background = self.backgrounds.get(jid)
        if jid in self.excluded:
            return {
                "status": "excluded",
                "code": "background_excluded",
                "reason": "公司背景不符合要求，已排除；不生成或发送招呼语",
            }
        if background and background.status != "pass":
            return {
                "status": "pending",
                "code": "background_pending",
                "reason": "公司背景仍待确认，暂不生成或发送招呼语：" + background.reason,
            }
        if jid not in self.assessments or background is None:
            return {
                "status": "pending",
                "code": "assessment_required",
                "reason": "尚未完成岗位与公司背景评分，暂不生成或发送招呼语",
            }
        score = self.assessments[jid].score
        if score < self.run.config.min_score:
            return {
                "status": "low_score",
                "code": "score_below_threshold",
                "reason": f"综合分 {score} 低于门槛 {self.run.config.min_score}，不生成或发送招呼语",
            }
        return None

    def assessed(self, jid):
        result = self.greeting_block(jid)
        if result:
            raise EligibilityBlocked(jid, result)

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
            self.receipt = None
            self.control.pause_requested.set()
            raise BrowserFailure("求职标签已变化，本次不发送；继续本轮时将恢复并核验目标标签")
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
            contact = event.payload.get("stages", {}).get("contact", {}).get("outcome")
            if (
                event.payload["status"] == "sent"
                or "returned_to_list" in event.payload
                or contact in {"succeeded", "unknown", "intent"}
            ):
                self.done.add(job_id)
                self.candidates_need_sync = True
                if not event.payload.get("returned_to_list", event.payload["status"] == "sent"):
                    self.receipt = None
                    self.control.pause_requested.set()
                else:
                    observed = await self.browser.preference_snapshot()
                    if await self.browser.verify_preferences(observed):
                        try:
                            await self.sync_refreshed_candidates(observed, job_id)
                        except BrowserFailure:
                            self.receipt = None
                            self.control.pause_requested.set()
                            raise
                        self.receipt = observed
                    else:
                        self.receipt = None
                        self.control.pause_requested.set()
            if event.payload["status"] in {"unknown", "session_changed", "needs_default_ack"}:
                self.control.pause_requested.set()
        if not any(e.payload["status"] in {"paused", "session_changed", "needs_default_ack"} for e in events):
            self.done.add(job_id)
        result = {"events": [asdict(e) for e in events]}
        if self.candidates_need_sync and self.control.pause_requested.is_set():
            result["reason"] = (
                "发送处理后尚未恢复推荐列表，请核对消息结果并恢复目标推荐页，再继续本轮；已联系或结果未知的岗位不会重发"
            )
        return result

    async def skip_job(self, job_id):
        self.done.add(job_id)
        self.publish("check", job_id=job_id, status="skipped", reasons=["AI 决定跳过，理由见步骤记录"])
        return {"status": "skipped"}

    async def finish(self):
        self.finished = True
        return {"status": "finished", "discovered": self.discovered_count(), "handled": len(self.done)}

    def summary(self):
        jid = self.active_job_id()
        current = None
        if jid in self.jobs and jid not in self.done:
            current = {
                "job": asdict(self.jobs[jid]),
                "sources": self.sources.get(jid, []),
                "source_ids": self.source_ids(jid),
                "company": self.companies.get(jid),
                "assessment": asdict(self.assessments[jid]) if jid in self.assessments else None,
                "background": asdict(self.backgrounds[jid]) if jid in self.backgrounds else None,
            }
        return {
            "current": current,
            "verified": bool(self.receipt),
            "active_job_id": jid,
            "pending_count": sum(j not in self.done for j in self.jobs),
            "handled_count": len(self.done),
            "discovered_count": self.discovered_count(),
            "list_ended": self.list_ended,
            "jobs": [
                {
                    "id": jid,
                    "title": job.title,
                    "company": job.company,
                    "city": job.city,
                    "salary": job.salary_raw,
                    "detail_read": jid in self.details,
                    "sources": len(self.sources.get(jid, [])),
                    "company_checked": jid in self.company_checked,
                    "assessed": jid in self.assessments,
                    "background_status": self.backgrounds[jid].status
                    if jid in self.backgrounds
                    else "unchecked",
                    "score": self.assessments[jid].score if jid in self.assessments else None,
                    "can_draft": jid not in self.done and self.greeting_block(jid) is None,
                    "greeting": jid in self.greetings,
                    "done": jid in self.done,
                }
                for jid, job in self.jobs.items()
                if jid == self.active_job_id()
            ],
        }
