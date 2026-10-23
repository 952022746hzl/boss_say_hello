import json

import pytest

from boss_say_hello.runtime.controller import Controller
from boss_say_hello.runtime.worker import WorkerThread
from boss_say_hello.storage.records import RecordStore
from boss_say_hello.ui.main_window import MainWindow


@pytest.mark.parametrize("close_immediately", [False, True])
@pytest.mark.parametrize("has_source", [False, True])
def test_resume_editor_draft_survives_restart_without_becoming_confirmed(
    qtbot, tmp_path, monkeypatch, resume, close_immediately, has_source
):
    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    if has_source:
        store = RecordStore(tmp_path / "records.sqlite3")
        store.save_resume(resume())
        store.close()
    worker = WorkerThread()
    worker.start()
    c = Controller(worker)
    window = MainWindow(c)
    qtbot.addWidget(window)
    path = tmp_path / "config" / "resume_draft.json"
    try:
        c.load_settings().result(3)
        qtbot.waitUntil(lambda: window.settings.open_workspace.isEnabled(), timeout=3000)
        window.resume.keywords.setPlainText("Python\nSQL\n")
        window.resume.summary.setPlainText("  修改中的经历摘要  ")
        assert not c.resume_ready
        if close_immediately:
            window.close()
            qtbot.waitUntil(lambda: not worker.isRunning(), timeout=3000)
        else:
            qtbot.waitUntil(path.exists, timeout=3000)
        assert json.loads(path.read_text(encoding="utf-8"))["summary"] == "  修改中的经历摘要  "
    finally:
        c.close()
        assert worker.wait(3000)

    worker2 = WorkerThread()
    worker2.start()
    c2 = Controller(worker2)
    reopened = MainWindow(c2)
    qtbot.addWidget(reopened)
    try:
        c2.load_settings()
        qtbot.waitUntil(lambda: reopened.resume.summary.toPlainText() == "  修改中的经历摘要  ", timeout=3000)
        assert reopened.resume.keywords.toPlainText() == "Python\nSQL\n"
        assert not c2.resume_ready
        assert not reopened.run.start_button.isEnabled()
        if has_source:
            reopened.resume.confirm()
            qtbot.waitUntil(lambda: c2.resume_ready, timeout=3000)
            assert reopened.resume.version.keywords == ("Python", "SQL")
            assert not path.exists()
    finally:
        c2.close()
        assert worker2.wait(3000)
