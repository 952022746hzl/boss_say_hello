import asyncio
import shutil
from pathlib import Path
from dataclasses import replace
from uuid import uuid4
from boss_say_hello.domain import ResumeVersion
from boss_say_hello.resume.pdf import extract_pdf


class ResumeService:
    def __init__(self, store, ai, managed_root: Path | None = None):
        self.store = store
        self.ai = ai
        self.managed_root = managed_root

    async def import_pdf(self, path: Path) -> ResumeVersion:
        digest, text = await asyncio.to_thread(extract_pdf, path)
        keywords, summary = await self.ai.extract_resume(text)
        version = ResumeVersion(uuid4().hex, digest, text, tuple(keywords), summary, False)
        if self.managed_root:
            self.managed_root.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, path, self.managed_root / f'{digest}.pdf')
        self.store.save_resume(version)
        return version

    def revise(self, version, keywords, summary):
        draft = replace(version, id=uuid4().hex, keywords=tuple(dict.fromkeys(k.strip() for k in keywords if k.strip())), summary=summary.strip(), confirmed=False)
        self.store.save_resume(draft)
        return draft

    def confirm(self, version):
        if not version.keywords or not version.summary.strip():
            raise ValueError('请填写关键词和经历摘要后确认')
        confirmed = replace(version, id=uuid4().hex, confirmed=True)
        self.store.save_resume(confirmed)
        return confirmed
