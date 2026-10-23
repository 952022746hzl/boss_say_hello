import json

import httpx
import pytest

from boss_say_hello.ai.client import AiService
from boss_say_hello.runtime.controller import Controller
from boss_say_hello.runtime.worker import WorkerThread
from boss_say_hello.storage.credentials import credential_scope
from boss_say_hello.ui.main_window import MainWindow
from tests.test_resume import make_pdf


@pytest.mark.parametrize("key_source", ["typed", "session", "system"])
def test_import_uses_current_unsaved_settings_through_ui_and_worker(
    qtbot, tmp_path, monkeypatch, config, key_source
):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    current = config(base_url="https://current.invalid/v1", model="current-model")
    requests = []

    def respond(request):
        requests.append((str(request.url), request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"keywords": ["Python"], "summary": "Synthetic Python project"}
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.AiService",
        lambda cfg, key: AiService(cfg, key, httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(
        "keyring.get_password",
        lambda service, scope: (
            "test-key" if key_source == "system" and scope == credential_scope(current) else None
        ),
    )
    monkeypatch.setattr("PyQt6.QtWidgets.QFileDialog.getOpenFileName", lambda *args: (str(path), "PDF"))
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    events = []
    controller.event.connect(events.append)
    try:
        window.settings.set_config(current)
        if key_source == "typed":
            window.settings.secret.setText("test-key")
        elif key_source == "session":
            controller.use_credential(current, "test-key").result(3)
        window.resume.choose_file()
        # Subsequent edits must not mutate the in-flight import's configuration.
        window.settings.base_url.setText("https://later.invalid/v1")
        window.settings.model.setText("later-model")
        qtbot.waitUntil(lambda: any(e.kind in ("resume", "ui_error") for e in events), timeout=3000)
        assert not [e for e in events if e.kind == "ui_error"]
        assert window.resume.version.summary == "Synthetic Python project"
        assert not window.resume.version.confirmed
        assert len(requests) == 1
        url, authorization, body = requests[0]
        assert url == "https://current.invalid/v1/chat/completions"
        assert authorization == "Bearer test-key"
        assert body["model"] == "current-model"
        assert "First page Python" in body["messages"][1]["content"]
        assert not (tmp_path / "settings.json").exists()
    finally:
        controller.close()
        assert worker.wait(3000)


def test_missing_model_settings_blocks_import_before_file_picker(qtbot, monkeypatch, resume):
    opened = []
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getOpenFileName", lambda *args: (opened.append(True) or "", "")
    )
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    try:
        controller.current_resume = resume()
        controller.resume_ready = True
        window.tabs.setCurrentWidget(window.resume)
        window.resume.choose_file()
        assert opened == []
        assert window.tabs.currentWidget() is window.settings
        assert window.settings.base_url.property("invalid") is True
        assert window.settings.model.property("invalid") is True
        assert controller.resume_ready
        assert "API 地址" in window.resume.status.text()
    finally:
        controller.close()
        assert worker.wait(3000)
