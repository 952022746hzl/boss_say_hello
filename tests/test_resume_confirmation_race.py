import asyncio
import json
import threading

import pytest

from boss_say_hello.domain import RunEvent
from boss_say_hello.runtime.backend import Backend
from boss_say_hello.runtime.controller import Controller
from boss_say_hello.runtime.worker import WorkerThread
from boss_say_hello.ui.main_window import MainWindow
from tests.fakes.browser import FakeBrowser


@pytest.mark.parametrize("fails", [False, True])
def test_confirmation_locks_edits_and_restores_saving(qtbot, tmp_path, resume, fails):
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    backend = Backend(worker.event.emit, tmp_path, FakeBrowser())
    controller.backend = backend
    worker.cleanups.append(backend.close)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    source = resume()
    release = threading.Event()
    original_confirm = backend.confirm_resume

    async def initialize():
        await backend.initialize()
        backend.store.save_resume(source)

    async def delayed_confirm(*args):
        while not release.is_set():
            await asyncio.sleep(0.01)
        if fails:
            raise ValueError("合成确认失败")
        await original_confirm(*args)

    backend.confirm_resume = delayed_confirm
    try:
        worker.submit(initialize()).result(3)
        controller.receive(RunEvent("", "resume", {"version": source, "generation": 0}))
        window.resume.confirm()
        assert not window.resume.summary.isEnabled()
        assert not window.resume.keywords.isEnabled()
        assert not window.resume.upload_button.isEnabled()
        assert not window.resume.confirm_button.isEnabled()
        release.set()
        qtbot.waitUntil(lambda: window.resume.summary.isEnabled(), timeout=3000)
        assert window.resume.version.id == (source.id if fails else controller.current_resume.id)
        if not fails:
            assert window.resume.version.id != source.id
        window.resume.summary.setPlainText("after confirmation edit")
        future = window.resume.flush_save()
        assert future.result(3) is True
        saved = json.loads((tmp_path / "config" / "resume_draft.json").read_text(encoding="utf-8"))
        assert saved["source_id"] == window.resume.version.id
        assert saved["summary"] == "after confirmation edit"
    finally:
        release.set()
        controller.close()
        assert worker.wait(3000)


async def test_draft_cleanup_failure_does_not_hide_confirmed_version(tmp_path, resume, monkeypatch):
    from pathlib import Path

    events = []
    backend = Backend(events.append, tmp_path, FakeBrowser())
    await backend.initialize()
    source = resume()
    backend.store.save_resume(source)
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == backend.draft_path:
            raise PermissionError("synthetic cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    try:
        await backend.confirm_resume(source, ("Python",), "new summary", 1)
        latest = backend.store.latest_resume()
        updated = next(e.payload["version"] for e in events if e.kind == "resume")
        assert latest == updated and updated.confirmed
        await backend.save_resume_draft(updated.id, "Python", "next edit", 2)
        assert json.loads(backend.draft_path.read_text(encoding="utf-8"))["source_id"] == updated.id
    finally:
        await backend.close()


async def test_failed_confirmation_commit_keeps_previous_version(tmp_path, resume, monkeypatch):
    backend = Backend(lambda e: None, tmp_path, FakeBrowser())
    await backend.initialize()
    source = resume()
    backend.store.save_resume(source)
    original_save = backend.store.save_resume

    def fail_confirmed(version):
        if version.confirmed:
            raise OSError("synthetic write failure")
        return original_save(version)

    monkeypatch.setattr(backend.store, "save_resume", fail_confirmed)
    try:
        with pytest.raises(OSError):
            await backend.confirm_resume(source, ("Python",), "changed", 1)
        assert backend.store.latest_resume().id == source.id
    finally:
        await backend.close()
