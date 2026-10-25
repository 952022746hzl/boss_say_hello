import asyncio
import json
import re
import time
from urllib.parse import urlparse

from boss_say_hello.browser import selectors as dom
from boss_say_hello.browser.actions import script
from boss_say_hello.browser.port import (
    ActionBlocked,
    ActionResult,
    AttachmentInfo,
    BrowserCapabilities,
    BrowserFailure,
    JobUnavailable,
    SessionInfo,
)
from boss_say_hello.diagnostics import failure, record
from boss_say_hello.domain import Job


def job_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.zhipin.com", "zhipin.com"):
        return ""
    match = re.fullmatch(r"/job_detail/([A-Za-z0-9_-]+)\.html", parsed.path)
    return match[1] if match else ""


def parse_job(account_id, card, detail):
    label = detail.get("contact", "")
    city = detail.get("city") or card.get("city") or None
    if city:
        city = re.split(r"[·•\s]", city)[0]
    return Job(
        account_id,
        job_id(card.get("url", "")),
        detail.get("recruiter_id") or card.get("recruiter_id"),
        detail.get("title") or card.get("title", ""),
        detail.get("company") or card.get("company", ""),
        city,
        detail.get("salary") or card.get("salary", ""),
        detail.get("text", ""),
        card.get("url", ""),
        "new" if label == "立即沟通" else "contacted" if label == "继续沟通" else "unknown",
        detail.get("company_id", ""),
        detail.get("company_full_name", ""),
        detail.get("company_url", ""),
        detail.get("recruiter_name", ""),
    )


class NodriverAdapter:
    poll_attempts = 5
    editor_poll_attempts = 20

    def __init__(self):
        self.browser = None
        self.list_tab = None
        self.detail_tab = None
        self.active_job = None
        self.control = None
        self.preference_state = None
        self.goal = None
        self.candidate_seen = set()

    def bind_goal(self, config):
        self.goal = config
        self.preference_state = None
        self.candidate_seen = set()

    def release_run(self):
        self.goal = None
        self.control = None
        self.preference_state = None
        self.candidate_seen.clear()
        self.active_job = None
        self.detail_tab = None

    async def preference_snapshot(self):
        from boss_say_hello.browser.preferences import SNAPSHOT

        self.preference_state = await self.evaluate(self.list_tab, SNAPSHOT)
        record(
            "browser.preference.snapshot",
            level="DEBUG",
            document=self.preference_state.get("document"),
            path=self.preference_state.get("path"),
            controls=self.preference_state.get("controls", []),
            selected=[e["label"] for e in self.preference_state.get("expectations", []) if e["selected"]],
        )
        return self.preference_state

    async def existing_expectations(self):
        from boss_say_hello.browser.preferences import EXISTING_EXPECTATIONS, parse_expectations

        return parse_expectations(await self.evaluate(self.list_tab, EXISTING_EXPECTATIONS))

    async def preference_action(self, option_id):
        from boss_say_hello.browser.preferences import action_script

        self.guard()
        if self.goal is None or self.preference_state is None:
            raise BrowserFailure("请先读取本轮求职标签控件")
        options = [c for c in self.preference_state["controls"] if c["id"] == option_id]
        if len(options) != 1:
            record("browser.preference.stale", level="WARNING", code="stale_control")
            current = await self.preference_snapshot()
            current["action_result"] = {
                "status": "stale_control",
                "message": "控件 ID 已过期或不存在，本次未操作；请使用本次返回的新控件 ID",
            }
            return current
        option = options[0]
        started = time.monotonic()
        record(
            "browser.preference.started",
            action=option["kind"],
            control_label=option["label"],
            control_id=option_id,
        )
        self.guard()
        result = await self.evaluate(self.list_tab, action_script(option_id))
        if not result.get("acted"):
            record(
                "browser.preference.stale",
                level="WARNING",
                code="control_unavailable",
                control_label=option["label"],
            )
            current = await self.preference_snapshot()
            current["action_result"] = {
                "status": "control_unavailable",
                "message": "控件已隐藏、遮挡或发生变化，本次未操作；请核对最新页面",
            }
            return current
        await asyncio.sleep(0.4)
        record(
            "browser.preference.verify",
            action=option["kind"],
            control_label=option["label"],
            control_id=option_id,
        )
        current = await self.preference_snapshot()
        current["action_result"] = {"status": "observed", "action": option["kind"], "label": option["label"]}
        record(
            "browser.preference.completed",
            action=option["kind"],
            control_label=option["label"],
            count=len(current["controls"]),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return current

    async def verify_preferences(self, receipt):
        from boss_say_hello.browser.preferences import preferences_match

        current = await self.preference_snapshot()
        return bool(
            self.goal
            and current["document"] == receipt.get("document")
            and current.get("path") == receipt.get("path")
            and preferences_match(current, self.goal)
        )

    async def company_info(self, job):
        raw = await self.read_detail(job.url)
        return {
            "id": raw.get("company_id", ""),
            "name": raw.get("company_full_name") or raw.get("company") or job.company,
            "city": job.city,
            "url": raw.get("company_url") or "",
            "text": raw.get("company_text", ""),
        }

    def bind_control(self, control):
        self.control = control

    def guard(self):
        if self.control is None or self.control.pause_requested.is_set() or self.control.closing.is_set():
            raise ActionBlocked("任务暂停或未绑定运行控制")

    async def refresh_job(self, job):
        session = await self.session()
        raw = await self.read_detail(job.url)
        return parse_job(session.account_id, {"url": job.url}, raw)

    @staticmethod
    def send_block_reason(code):
        return {
            "target_changed": "目标会话已变化，未发送",
            "draft_empty": "填写后输入框再次变空，未发送；请核对页面",
            "draft_changed": "输入框内容与本次草稿不同，已停止且未覆盖内容",
            "existing_draft": "输入框已有其他草稿，未覆盖、未发送",
            "input_limit_unverified": "输入框或长度限制无法核实，未发送",
            "send_control_not_ready": "等待发送按钮就绪超时，未点击发送",
            "send_control_unverified": "无法唯一核实发送按钮，未发送",
            "matching_message_appeared": "观察到新增的同文消息，已停止自动操作以避免重复发送；结果待核实",
        }.get(code, "页面控件或目标身份无法核实，未执行")

    def blocked_send(self, code):
        return ActionResult(
            "unknown" if code == "matching_message_appeared" else "failed", self.send_block_reason(code)
        )

    async def wait_message_ready(self, job, value, baseline_ids):
        stable = 0
        refilled = False
        for index in range(self.editor_poll_attempts):
            self.guard()
            state = await self.evaluate(
                self.detail_tab, script("message_ready", job, value, baseline_ids=baseline_ids)
            )
            record(
                "browser.editor.wait",
                level="DEBUG",
                job_id=job.id,
                poll=index + 1,
                **{
                    k: state.get(k)
                    for k in ("ready", "code", "matched", "text_matches", "button_enabled", "char_count")
                },
            )
            if state.get("ready"):
                stable += 1
                if stable >= 2:
                    return None
            else:
                stable = 0
                code = state.get("code")
                if code == "draft_empty" and not refilled:
                    self.guard()
                    record("browser.editor.refill", job_id=job.id)
                    filled = await self.evaluate(
                        self.detail_tab, script("fill_message", job, value, baseline_ids=baseline_ids)
                    )
                    refilled = True
                    if not filled.get("filled"):
                        return self.blocked_send(filled.get("code"))
                elif code != "send_control_not_ready":
                    return self.blocked_send(code)
            if index + 1 < self.editor_poll_attempts:
                await asyncio.sleep(0.25)
        return ActionResult("failed", self.send_block_reason("send_control_not_ready"))

    async def _action(self, job, action, value=""):
        self.guard()
        session = await self.session()
        if not session.logged_in or session.account_id != job.account_id:
            return ActionResult("failed", "当前账号不匹配，未执行", attempted=False)
        observe = "observe_attachment" if action == "attachment" else "observe"
        before = {"count": 0, "seen_ids": []}
        old_tabs = {t.target_id for t in self.browser.tabs} if self.browser else set()
        if action != "contact":
            before = await self.evaluate(self.detail_tab, script(observe, job, value))
            if not before.get("matched"):
                return ActionResult("failed", "无法核实目标会话，未执行")
        if action == "message":
            self.guard()
            record("browser.delivery.started", action="fill_message", job_id=job.id, char_count=len(value))
            filled = await self.evaluate(
                self.detail_tab, script("fill_message", job, value, baseline_ids=before.get("seen_ids", []))
            )
            if not filled.get("filled"):
                record("browser.editor.wait", level="DEBUG", job_id=job.id, code=filled.get("code"))
                return self.blocked_send(filled.get("code"))
            blocked = await self.wait_message_ready(job, value, before.get("seen_ids", []))
            if blocked:
                return blocked
        self.guard()
        session = await self.session()
        if not session.logged_in or session.account_id != job.account_id:
            return ActionResult("failed", "当前账号不匹配，未执行", attempted=False)
        self.guard()
        operation = "send_message" if action == "message" else action
        record("browser.delivery.started", action=operation, job_id=job.id)
        clicked = await self.evaluate(
            self.detail_tab,
            script(
                operation,
                job,
                value,
                baseline_ids=before.get("seen_ids", []) if action == "message" else None,
            ),
        )
        if not isinstance(clicked, dict) or type(clicked.get("clicked")) is not bool:
            return ActionResult("unknown", "点击回执缺失或格式异常，结果待核实；不会自动重发")
        if clicked["clicked"] is False:
            record(
                "browser.delivery.observed",
                level="DEBUG",
                job_id=job.id,
                action=operation,
                clicked=False,
                code=clicked.get("code"),
            )
            blocked = self.blocked_send(clicked.get("code"))
            return ActionResult(blocked.outcome, blocked.detail, attempted=False)
        continued = False
        attempts = (
            max(self.poll_attempts, 100 if action == "contact" else 40)
            if self.browser
            else self.poll_attempts
        )
        for index in range(attempts):
            # Contact may already have sent the platform greeting: never release its reservation.
            if self.control.pause_requested.is_set() or self.control.closing.is_set():
                return ActionResult("unknown", "操作后任务暂停，结果待核实；不会自动重发")
            candidates = [self.detail_tab]
            if action == "contact" and self.browser:
                await self.browser.update_targets()
                candidates.extend(
                    t
                    for t in self.browser.tabs
                    if t.target_id not in old_tabs
                    and t is not self.detail_tab
                    and urlparse(t.url).hostname in {"www.zhipin.com", "zhipin.com"}
                )
            for tab in candidates:
                evidence = await self.evaluate(tab, script(observe, job, value))
                record(
                    "browser.delivery.observed",
                    level="DEBUG",
                    job_id=job.id,
                    action=action,
                    poll=index + 1,
                    matched=evidence.get("matched"),
                    count=evidence.get("count"),
                    before_count=before.get("count"),
                )
                if evidence.get("matched") and (
                    (action == "contact" and evidence.get("input_ready", True))
                    or (
                        action != "contact"
                        and bool(set(evidence.get("ids", [])) - set(before.get("seen_ids", [])))
                    )
                ):
                    self.detail_tab = tab
                    record("browser.delivery.completed", job_id=job.id, action=action, status="succeeded")
                    return ActionResult(
                        "succeeded",
                        "已核实目标岗位与招聘者会话"
                        if action == "contact"
                        else "已观察到新增己方消息及平台送达标记",
                    )
                if action == "contact" and evidence.get("can_continue") and not continued:
                    if self.control.pause_requested.is_set() or self.control.closing.is_set():
                        return ActionResult("unknown", "首次联系后暂停，未继续操作；结果待核实")
                    record("browser.delivery.started", action="continue_contact", job_id=job.id)
                    result = await self.evaluate(tab, script("continue_contact", job))
                    continued = bool(result.get("clicked"))
                    if not continued:
                        return ActionResult("unknown", "首次联系后弹框状态变化，请手动核实")
            if index + 1 < attempts:
                await asyncio.sleep(0.5)
        return ActionResult("unknown", "已操作，但目标会话或送达证据不足；不会自动重试")

    async def open_contact(self, job):
        return await self._action(job, "contact")

    async def send_text(self, job, text):
        return await self._action(job, "message", text)

    async def send_attachment(self, job, attachment_id):
        return await self._action(job, "attachment", attachment_id)

    async def return_to_list(self):
        if self.list_tab is None:
            return False
        # The site may navigate in the current tab or open a new one.
        current = await self.evaluate(
            self.list_tab, "/* LIST_LOCATION */ JSON.stringify({path:location.pathname})"
        )
        if current.get("path") not in {"/web/geek/job-recommend", "/web/geek/jobs"}:
            await self.list_tab.get(dom.RECOMMEND_URL)
            for _ in range(20):
                if (await self.preference_snapshot()).get("expectations"):
                    break
                await asyncio.sleep(0.5)
            else:
                return False
        if self.goal:
            from boss_say_hello.browser.preferences import compact, preferences_match

            observed = await self.preference_snapshot()
            if not preferences_match(observed, self.goal):
                label = compact(f"{self.goal.target_title}({self.goal.cities[0]})")
                controls = [c for c in observed.get("controls", []) if compact(c["label"]) == label]
                if len(controls) != 1:
                    return False
                observed = await self.preference_action(controls[0]["id"])
                if not preferences_match(observed, self.goal):
                    return False
        await self.list_tab.activate()
        return True

    async def evaluate(self, tab, script):
        if tab is None:
            raise BrowserFailure("请先打开浏览器并手动登录")
        started = time.monotonic()
        marker = re.match(r"/\* (\w+) \*/", script)
        operation = marker.group(1) if marker else "DOM"
        record("browser.read.started", level="DEBUG", operation=operation)
        try:
            value = await asyncio.wait_for(tab.evaluate(script), 12)
            record(
                "browser.read.succeeded",
                level="DEBUG",
                operation=operation,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            return json.loads(value) if isinstance(value, str) else value
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure(
                "browser.read.failed",
                exc,
                operation=operation,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise BrowserFailure("页面读取失败，请检查浏览器或重新登录") from None

    async def open(self, profile):
        self.discard_exited_browser()
        if self.browser is None:
            import nodriver

            profile.mkdir(parents=True, exist_ok=True)
            self.browser = await nodriver.start(user_data_dir=str(profile), headless=False)
            self.list_tab = await self.browser.get(dom.RECOMMEND_URL)
        else:
            await self.list_tab.get(dom.RECOMMEND_URL)

    async def session(self):
        self.discard_exited_browser()
        if self.list_tab is None:
            return SessionInfo(None, False)
        raw = await self.evaluate(self.list_tab, dom.SESSION)
        return SessionInfo(raw.get("account_id"), bool(raw.get("logged_in")))

    def discard_exited_browser(self):
        process = getattr(self.browser, "_process", None)
        if process is not None and getattr(process, "returncode", None) is not None:
            self.browser = self.list_tab = self.detail_tab = self.active_job = None
            if self.control:
                self.control.pause_requested.set()
            record("browser.closed")

    async def iter_recommended(self):
        seen = set()
        misses = 0
        while True:
            session = await self.session()
            if not session.logged_in or not session.account_id:
                raise BrowserFailure("登录失效或无法核实当前账号标识，请重新检查登录")
            snapshot = await self.evaluate(self.list_tab, dom.CARDS)
            fresh = [card for card in snapshot.get("cards", []) if card.get("url") not in seen]
            for card in fresh:
                seen.add(card.get("url"))
                if not job_id(card.get("url", "")):
                    yield parse_job(session.account_id, card, {})
                    continue
                detail = await self.read_detail(card["url"])
                yield parse_job(session.account_id, card, detail)
            if snapshot.get("ended"):
                return
            misses = 0 if fresh else misses + 1
            if misses >= self.poll_attempts:
                raise BrowserFailure("推荐列表未继续加载，无法确认是否到底；本轮已停止")
            await self.evaluate(self.list_tab, dom.SCROLL)
            await asyncio.sleep(1)

    async def candidate_batch(self):
        session = await self.session()
        if not session.logged_in or not session.account_id:
            raise BrowserFailure("登录失效")
        for _attempt in range(self.poll_attempts):
            snapshot = await self.evaluate(self.list_tab, dom.CARDS)
            cards = [
                c
                for c in snapshot.get("cards", [])
                if c.get("url") not in self.candidate_seen and job_id(c.get("url", ""))
            ]
            batch = cards[:5]
            self.candidate_seen.update(c["url"] for c in batch)
            if batch or snapshot.get("ended"):
                return {
                    "jobs": [parse_job(session.account_id, c, {}) for c in batch],
                    "ended": bool(snapshot.get("ended")) and len(cards) <= 5,
                }
            self.guard()
            await self.evaluate(self.list_tab, dom.SCROLL)
            await asyncio.sleep(1)
        raise BrowserFailure("列表未继续加载，无法确认是否到底，请检查页面")

    async def current_candidate_ids(self):
        for attempt in range(self.poll_attempts):
            self.guard()
            snapshot = await self.evaluate(self.list_tab, dom.CARDS)
            ids = {job_id(c.get("url", "")) for c in snapshot.get("cards", [])} - {""}
            if ids or snapshot.get("ended"):
                return ids
            if attempt + 1 < self.poll_attempts:
                await asyncio.sleep(0.5)
        raise BrowserFailure("刷新后的推荐列表尚未加载，无法同步候选岗位")

    async def open_preferences(self):
        self.guard()
        await self.list_tab.get(dom.RECOMMEND_URL)
        await asyncio.sleep(1)
        return await self.preference_snapshot()

    async def read_detail(self, url):
        if not job_id(url):
            raise BrowserFailure("岗位链接无法核实")
        # Current BOSS uses an in-list detail pane. Standalone SEO pages omit
        # reliable recruiter identity and contact state, so do not use them.
        if not await self.evaluate(self.list_tab, dom.select_job(url)):
            snapshot = await self.evaluate(self.list_tab, dom.CARDS)
            ids = {job_id(card.get("url", "")) for card in snapshot.get("cards", [])} - {""}
            if ids and job_id(url) not in ids:
                raise JobUnavailable("岗位已不在当前推荐列表，本轮跳过并继续处理其他岗位")
            raise BrowserFailure("当前推荐列表中找不到此岗位，请重新加载推荐页")
        self.detail_tab = self.list_tab
        for _ in range(20):
            raw = await self.evaluate(self.detail_tab, dom.DETAIL)
            if job_id(raw.get("url", "")) == job_id(url) and raw.get("text"):
                return raw
            await asyncio.sleep(0.5)
        raise BrowserFailure("岗位详情未能正确加载")

    async def attachments(self):
        raw = await self.evaluate(self.detail_tab or self.list_tab, dom.ATTACHMENTS)
        return list(
            {
                item["id"]: AttachmentInfo(item["id"], item["name"])
                for item in raw
                if item.get("id") and item.get("name")
            }.values()
        )

    async def capabilities(self):
        if self.detail_tab is None and self.list_tab is None:
            return BrowserCapabilities(None, "unknown")
        raw = await self.evaluate(self.detail_tab or self.list_tab, dom.CAPABILITIES)
        return BrowserCapabilities(
            raw.get("greeting_limit"),
            raw.get("initial_contact_behavior", "unknown"),
            raw.get("limit_source", "editor"),
        )

    async def close(self):
        if self.browser:
            process = self.browser._process
            self.browser.stop()
            await self.browser.aclose()
            if process:
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 3)
        self.browser = self.list_tab = self.detail_tab = None
