import asyncio
import threading

import httpx
import pytest

from boss_say_hello.ai.client import AiService
from boss_say_hello.runtime.controller import Controller
from boss_say_hello.runtime.worker import WorkerThread
from boss_say_hello.storage.records import RecordStore
from boss_say_hello.ui.main_window import MainWindow
from tests.test_resume import make_pdf


@pytest.mark.parametrize("succeeds", [True, False])
def test_import_locks_editor_and_restores_it_after_success_or_failure(
    qtbot, tmp_path, monkeypatch, config, resume, succeeds
):
    path = tmp_path / "new.pdf"
    make_pdf(path)
    store = RecordStore(tmp_path / "records.sqlite3")
    store.save_resume(resume())
    store.close()
    started, release = threading.Event(), threading.Event()

    async def respond(request):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        if not succeeds:
            return httpx.Response(500, json={"error": {"message": "unavailable"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"keywords":["New"],"summary":"New summary"}'}}]}
        )

    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.AiService",
        lambda cfg, key: AiService(cfg, key, httpx.MockTransport(respond)),
    )
    monkeypatch.setattr("PyQt6.QtWidgets.QFileDialog.getOpenFileName", lambda *args: (str(path), "PDF"))
    worker = WorkerThread()
    worker.start()
    c = Controller(worker)
    window = MainWindow(c)
    qtbot.addWidget(window)
    try:
        c.load_settings()
        qtbot.waitUntil(lambda: window.resume.version is not None, timeout=3000)
        window.settings.set_config(config())
        window.settings.secret.setText("test-key")
        window.resume.summary.setPlainText("Saved user edit")
        window.resume.choose_file()
        qtbot.waitUntil(started.is_set, timeout=3000)
        assert not window.resume.summary.isEnabled()
        assert not window.resume.keywords.isEnabled()
        assert not window.resume.upload_button.isEnabled()
        qtbot.keyClicks(window.resume.summary, "should not replace the draft")
        assert window.resume.summary.toPlainText() == "Saved user edit"
        release.set()
        qtbot.waitUntil(lambda: window.resume.summary.isEnabled(), timeout=3000)
        assert window.resume.summary.toPlainText() == ("New summary" if succeeds else "Saved user edit")
        assert window.resume.confirm_button.isEnabled()
        assert (tmp_path / "config" / "resume_draft.json").exists() is not succeeds
    finally:
        release.set()
        c.close()
        assert worker.wait(3000)
