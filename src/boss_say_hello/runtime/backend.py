"""Owned by the worker's asyncio loop; widgets never access these stores."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from boss_say_hello.ai.client import AiService
from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.delivery.service import DeliveryService
from boss_say_hello.domain import RunEvent, RunSnapshot
from boss_say_hello.greetings.service import GreetingService
from boss_say_hello.matching.service import MatchingService
from boss_say_hello.resume.service import ResumeService
from boss_say_hello.runs.control import Clock
from boss_say_hello.runs.engine import RunEngine
from boss_say_hello.storage.credentials import CredentialStore
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

    def notice(self, kind, **payload):
        self.emit(RunEvent("", kind, payload))

    async def initialize(self):
        async with self.lock:
            if self.initialized:
                return
            self.root = self.root or data_root()
            self.settings = ConfigStore(self.root)
            self.config = self.settings.load()
            self.store = RecordStore(self.root / "records.sqlite3")
            self.journal = DeliveryJournal(self.store)
            self.journal.recover_incomplete()
            self.initialized = True

    async def load_settings(self):
        self.notice("settings", config=self.config)
        version = self.store.latest_resume()
        if version:
            self.notice("resume", version=version, generation=0)
        self.notice("notice", message=f"本地数据目录：{self.root}")

    async def save_settings(self, config):
        self.config = self.settings.save(config)
        self.notice("settings", config=self.config)
        self.notice("notice", message="配置已保存，仅影响下一轮任务")

    async def use_credential(self, id, secret):
        if not secret.strip():
            raise ValueError("请输入非空密钥")
        self.secrets[id] = secret
        self.notice("notice", message="密钥仅保存在当前会话内存中")

    async def save_credential(self, id, secret):
        await self.use_credential(id, secret)
        await asyncio.to_thread(self.credentials.save, id, secret)
        self.notice("notice", message="密钥已保存到系统凭据存储")

    async def secret(self, id):
        if id in self.secrets:
            return self.secrets[id]
        return await asyncio.to_thread(self.credentials.get, id) or ""

    async def test_connection(self, config, secret):
        ai = AiService(config, secret or await self.secret(config.credential_id))
        try:
            await ai.test_connection()
        finally:
            await ai.close()
        self.notice("notice", message="连接测试成功；未发送简历，未自动保存密钥")

    async def import_resume(self, path, generation):
        config = self.config
        ai = AiService(config, await self.secret(config.credential_id))
        self.notice("notice", message="正在提取 PDF 并生成关键词、摘要，请稍候")
        try:
            version = await ResumeService(self.store, ai, self.root / "resumes").import_pdf(path)
        finally:
            await ai.close()
        self.notice("resume", version=version, generation=generation)

    async def confirm_resume(self, version, keywords, summary, generation):
        service = ResumeService(self.store, None)
        # Resolve against the stored source, not a caller-supplied replacement PDF text.
        source = self.store.get_resume(version.id)
        draft = service.revise(source, keywords, summary)
        confirmed = service.confirm(draft)
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
                attachments=await self.browser.attachments(),
            )

    async def load_history(self, run_id):
        if run_id is None:
            self.notice("history_runs", runs=self.store.list_runs())
        else:
            self.notice("history_events", events=self.store.events(run_id), id=run_id)

    async def run(self, config, resume, control):
        if self.busy:
            raise ValueError("已有运行中的任务")
        config.validate(ready=True)
        if not resume.confirmed:
            raise ValueError("请先确认简历")
        self.busy = True
        ai = None
        try:
            session = await self.browser.session()
            if not session.logged_in or not session.account_id:
                raise ValueError("请先登录并核实账号标识")
            if config.auto_send:
                caps = await self.browser.capabilities()
                if caps.initial_contact_behavior == "unknown" or not caps.greeting_limit:
                    raise ValueError("平台首次沟通行为或长度限制未核实，请关闭自动发送后运行预览")
                if config.send_attachment and config.attachment_id not in {
                    a.id for a in await self.browser.attachments()
                }:
                    raise ValueError("选定平台附件不可用，请重新检查登录状态和附件")
            # Hold this client's key and configuration until this run ends.
            ai = AiService(config, await self.secret(config.credential_id))
            run = RunSnapshot(
                uuid4().hex, session.account_id, config, resume, datetime.now(timezone.utc).isoformat()
            )
            engine = RunEngine(
                self.store,
                self.browser,
                MatchingService(ai),
                GreetingService(ai),
                DeliveryService(self.browser, self.journal),
                self.journal,
                Clock(),
                self.emit,
            )
            await engine.run(run, control)
        finally:
            if ai:
                await ai.close()
            self.busy = False

    async def close(self):
        try:
            await self.browser.close()
        finally:
            if self.store:
                self.store.close()
            self.secrets.clear()
