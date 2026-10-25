"""Owned by the worker's asyncio loop; widgets never access these stores."""

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from boss_say_hello.agent.contracts import validate_agent_goal
from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.agent.search import CompanySearch
from boss_say_hello.agent.tools import ToolExecutor
from boss_say_hello.ai.agent_client import AgentClient
from boss_say_hello.ai.client import AiService
from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.delivery.service import DeliveryService
from boss_say_hello.diagnostics import EventLog
from boss_say_hello.domain import RunEvent, RunSnapshot
from boss_say_hello.resume.service import ResumeService
from boss_say_hello.runs.control import Clock
from boss_say_hello.runtime.control import RunStartBlocked
from boss_say_hello.storage.companies import CompanyCache
from boss_say_hello.storage.credentials import (
    CredentialStore,
    CredentialUnavailable,
    credential_scope,
    normalize_secret,
)
from boss_say_hello.storage.json_file import write_json
from boss_say_hello.storage.paths import data_root
from boss_say_hello.storage.records import RecordStore
from boss_say_hello.storage.settings import ConfigStore


class Backend:
    def __init__(self, emit, root=None, browser=None):
        self.emit = emit
        self.root = root
        self.browser = browser or NodriverAdapter()
        self.secrets = {}
        self.store = None
        self.initialized = False
        self.busy = False
        self.lock = asyncio.Lock()
        self.credentials = CredentialStore()
        self.log = None

    def emit_event(self, event):
        if self.store and event.run_id and event.kind != "state":
            self.export_history()
        if self.log:
            self.log.event(event)
        self.emit(event)

    def export_history(self):
        try:
            self.store.export_history(self.root / "history" / "jobs.json")
        except OSError as exc:
            # The SQLite journal remains authoritative even if the readable export fails.
            if self.log:
                self.log.failure("history.export_failed", exc)

    def notice(self, kind, **payload):
        self.emit_event(RunEvent("", kind, payload))

    async def initialize(self):
        async with self.lock:
            if self.initialized:
                return
            self.root = self.root or data_root()
            self.log = EventLog(
                self.root / "logs", lambda entry: self.emit(RunEvent("", "log", {"entry": entry}))
            )
            self.notice("logs_ready", path=self.log.root, entries=self.log.tail())
            self.settings = ConfigStore(self.root / "config")
            self.draft_path = self.root / "config" / "resume_draft.json"
            self.config = self.settings.load()
            self.store = RecordStore(self.root / "records.sqlite3")
            self.journal = DeliveryJournal(self.store)
            self.journal.recover_incomplete()
            self.export_history()
            self.initialized = True
            self.log.record("journal.recovered")
            self.log.record("app.ready")

    async def load_settings(self):
        self.notice("settings", config=self.config)
        self.notice("workspace", path=self.root)
        if self.config.base_url:
            try:
                secret = await self.secret(self.config)
                if secret:
                    self.notice("credential", scope=credential_scope(self.config), secret=secret)
            except CredentialUnavailable:
                self.notice("notice", message="无法读取系统凭据，请在配置页重新输入密钥并测试连接")
        version = self.store.latest_resume()
        if version:
            self.notice("resume", version=version, generation=0)
        if self.draft_path.exists():
            draft = json.loads(self.draft_path.read_text(encoding="utf-8"))
            if draft["source_id"] == (version.id if version else None):
                self.notice("resume_draft", draft=draft)
        await self.load_history(None)
        self.notice("notice", message=f"本地数据目录：{self.root}")

    async def save_settings(self, config):
        self.config = self.settings.save(config)
        self.notice("settings_saved", config=self.config)

    def credential_config(self, config):
        # Never read a legacy credential that was not bound to an endpoint.
        if isinstance(config, str):
            if config != self.config.credential_id:
                raise ValueError("密钥配置不匹配")
            return self.config
        return config

    async def use_credential(self, config, secret):
        secret = normalize_secret(secret)
        self.secrets[credential_scope(self.credential_config(config))] = secret
        self.notice("notice", message="密钥已绑定当前 API 地址，仅保存在本次会话内存中")

    async def save_credential(self, config, secret):
        config = self.credential_config(config)
        await self.use_credential(config, secret)
        await asyncio.to_thread(self.credentials.save, credential_scope(config), secret)
        self.notice("notice", message="密钥已保存到系统凭据存储")

    async def secret(self, config):
        scope = credential_scope(config)
        if scope in self.secrets:
            return self.secrets[scope]
        return await asyncio.to_thread(self.credentials.get, scope) or ""

    async def test_connection(self, config, secret):
        config.validate(ready=True)
        await self.save_settings(config)
        secret = secret or await self.secret(config)
        ai = AiService(config, secret)
        try:
            await ai.test_connection()
        finally:
            await ai.close()
        if secret:
            await self.use_credential(config, secret)
            try:
                await asyncio.to_thread(self.credentials.save, credential_scope(config), secret)
            except CredentialUnavailable:
                self.notice(
                    "notice", message="连接测试成功，但系统凭据保存失败；密钥仅本次有效，下次需重新输入"
                )
                return
        self.notice("notice", message="连接测试成功，配置和密钥已自动保存，下次启动会恢复")

    async def import_resume(self, path, generation, config, secret=""):
        config.validate(ready=True)
        ai = AiService(config, secret or await self.secret(config))
        self.notice("notice", message="正在提取 PDF 并生成关键词、摘要，请稍候")
        try:
            version = await ResumeService(self.store, ai, self.root / "resumes").import_pdf(path)
        finally:
            await ai.close()
        self.notice("resume", version=version, generation=generation)
        self.draft_path.unlink(missing_ok=True)

    async def save_resume_draft(self, source_id, keywords, summary, generation):
        latest = self.store.latest_resume()
        if source_id != (latest.id if latest else None):
            raise ValueError("简历来源已变化，草稿尚未保存；请重新载入当前简历后编辑")
        write_json(self.draft_path, {"source_id": source_id, "keywords": keywords, "summary": summary})
        self.notice("resume_draft_saved", generation=generation)

    async def confirm_resume(self, version, keywords, summary, generation):
        service = ResumeService(self.store, None)
        # Resolve against the stored source, not a caller-supplied replacement PDF text.
        source = self.store.get_resume(version.id)
        draft = service.revise(source, keywords, summary, persist=False)
        confirmed = service.confirm(draft)
        try:
            self.draft_path.unlink(missing_ok=True)
        except OSError as exc:
            # Its old source_id cannot override the newly confirmed version on load.
            self.log.record("resume.draft.cleanup_failed", level="WARNING", error_type=type(exc).__name__)
        self.notice("resume", version=confirmed, generation=generation)

    async def open_login(self):
        if self.busy:
            raise ValueError("请结束本轮后再重新打开登录页；可在现有窗口手动恢复登录")
        await self.browser.open(self.root / "browser_profile")
        await self.check_login()

    async def check_login(self):
        session = await self.browser.session()
        self.notice("session", session=session)
        if session.logged_in:
            self.notice(
                "platform",
                caps=await self.browser.capabilities(),
            )
            if not self.busy:
                await self.refresh_expectations()
        else:
            self.notice("expectations", account_id=None, items=[])

    async def refresh_expectations(self):
        if self.busy:
            raise ValueError("请结束本轮后再刷新求职标签")
        session = await self.browser.session()
        self.notice("session", session=session)
        self.notice("expectations", account_id=session.account_id, items=[])
        if not session.logged_in or not session.account_id:
            raise ValueError("请先登录 BOSS，再刷新已有求职标签")
        items = await self.browser.existing_expectations()
        current = await self.browser.session()
        if not current.logged_in or current.account_id != session.account_id:
            self.notice("session", session=current)
            self.notice("expectations", account_id=None, items=[])
            raise ValueError("登录账号已变化，请重新检查登录状态")
        self.notice("expectations", account_id=session.account_id, items=items)
        self.notice(
            "notice",
            message=f"已读取 {len(items)} 个求职标签，请从下拉框选择"
            if items
            else "未读到求职标签，请在 BOSS 打开岗位推荐页，确认已有求职期望后点击刷新标签",
        )

    async def load_history(self, run_id):
        self.export_history()
        if run_id is None:
            self.notice("history_runs", runs=self.store.list_runs())
        else:
            self.notice("history_events", events=self.store.events(run_id), id=run_id)

    async def run(self, config, resume, control):
        if self.busy:
            raise ValueError("已有运行中的任务")
        validate_agent_goal(config)
        if not resume.confirmed:
            raise ValueError("请先确认简历")
        self.busy = True
        ai = None
        search = None
        try:
            session = await self.browser.session()
            if not session.logged_in or not session.account_id:
                raise ValueError("请先登录并核实账号标识")
            if config.auto_send:
                caps = await self.browser.capabilities()
                if caps.initial_contact_behavior == "unknown" or not caps.greeting_limit:
                    raise RunStartBlocked(
                        "send_capabilities_unknown",
                        "本轮尚未启动：当前 BOSS 页面未能核实首次沟通行为或消息长度限制。请关闭「自动发送」后运行预览；未发送消息。",
                    )
                if caps.initial_contact_behavior in {"default_message", "may_default_message"}:
                    control.default_message_required.set()
                    control.pause_requested.set()
            # Hold this client's key and configuration until this run ends.
            key = await self.secret(config)
            ai = AgentClient(config, key)
            search = CompanySearch(config, key)
            run = RunSnapshot(
                uuid4().hex, session.account_id, config, resume, datetime.now(timezone.utc).isoformat()
            )
            executor = ToolExecutor(
                run,
                self.browser,
                ai,
                search,
                CompanyCache(self.root / "history" / "companies.json"),
                self.store,
                DeliveryService(self.browser, self.journal),
                self.journal,
                control,
                Clock(),
                self.emit_event,
            )
            await AgentEngine(executor, ai).run()
        finally:
            try:
                if ai:
                    await ai.close()
            finally:
                try:
                    if search:
                        await search.close()
                finally:
                    self.browser.release_run()
                    self.busy = False
                    self.log.record("run.cleaned")

    async def close(self):
        try:
            await self.browser.close()
        finally:
            if self.store:
                self.store.close()
            self.secrets.clear()
            if self.log:
                self.log.record("app.closed")
                self.log.close()
