import json

import httpx
import pytest

from boss_say_hello.ai.client import AiService
from boss_say_hello.domain import AppConfig
from boss_say_hello.runtime.controller import Controller
from boss_say_hello.runtime.worker import WorkerThread
from boss_say_hello.ui.main_window import MainWindow


async def test_failed_connection_preserves_previously_saved_key(tmp_path, config, monkeypatch):
    from boss_say_hello.ai.schemas import AiFailure
    from boss_say_hello.runtime.backend import Backend
    from boss_say_hello.storage.credentials import credential_scope
    from tests.fakes.browser import FakeBrowser

    current = config()
    saved = {credential_scope(current): "previous-key"}
    monkeypatch.setattr("keyring.get_password", lambda service, scope: saved.get(scope))
    monkeypatch.setattr("keyring.set_password", lambda service, scope, key: saved.update({scope: key}))
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.AiService",
        lambda cfg, key: AiService(
            cfg,
            key,
            httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "denied"}})),
        ),
    )
    backend = Backend(lambda event: None, root=tmp_path, browser=FakeBrowser())
    await backend.initialize()
    try:
        with pytest.raises(AiFailure):
            await backend.test_connection(current, "wrong-key")
        assert saved == {credential_scope(current): "previous-key"}
        assert backend.settings.load().base_url == current.base_url
    finally:
        await backend.close()


def test_connection_saves_current_configuration_and_restores_masked_key(qtbot, tmp_path, monkeypatch):
    saved_keys = {}
    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    monkeypatch.setattr("keyring.set_password", lambda service, scope, key: saved_keys.update({scope: key}))
    monkeypatch.setattr("keyring.get_password", lambda service, scope: saved_keys.get(scope))
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.AiService",
        lambda cfg, key: AiService(
            cfg,
            key,
            httpx.MockTransport(
                lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
            ),
        ),
    )
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    try:
        window.settings.base_url.setText("https://model.invalid/v1")
        window.settings.model.setText("saved-model")
        window.settings.secret.setText("test-key")
        window.settings.prompts[0].setPlainText("我的偏好")
        window.settings.test()
        qtbot.waitUntil(lambda: "连接测试成功" in window.settings.status.text(), timeout=3000)
        assert list(saved_keys.values()) == ["test-key"]
        path = tmp_path / "config" / "settings.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        assert config["model"] == "saved-model"
        assert config["prompts"]["preference"] == "我的偏好"
        assert "test-key" not in path.read_text(encoding="utf-8")
    finally:
        controller.close()
        assert worker.wait(3000)

    worker2 = WorkerThread()
    worker2.start()
    c2 = Controller(worker2)
    reopened = MainWindow(c2)
    qtbot.addWidget(reopened)
    try:
        c2.load_settings()
        qtbot.waitUntil(lambda: reopened.settings.secret.text() == "test-key", timeout=3000)
        assert reopened.settings.model.text() == "saved-model"
        assert reopened.settings.prompts[0].toPlainText() == "我的偏好"
        assert reopened.settings.secret.echoMode() == reopened.settings.secret.EchoMode.Password
    finally:
        c2.close()
        assert worker2.wait(3000)


def test_field_edits_autosave_without_resetting_newer_input(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    path = tmp_path / "config" / "settings.json"
    try:
        window.settings.cities.setText("上海")
        window.settings.prompts[1].setPlainText("我的评分标准")
        qtbot.waitUntil(path.exists, timeout=3000)
        qtbot.waitUntil(
            lambda: json.loads(path.read_text(encoding="utf-8"))["cities"] == ["上海"], timeout=3000
        )
        window.settings.cities.setText("杭州")
        # A delayed previous acknowledgement must not replace the current edits.
        from boss_say_hello.domain import RunEvent

        window.on_event(RunEvent("", "settings_saved", {"config": AppConfig(cities=("上海",))}))
        assert window.settings.cities.text() == "杭州"
        qtbot.waitUntil(
            lambda: json.loads(path.read_text(encoding="utf-8"))["cities"] == ["杭州"], timeout=3000
        )
        assert json.loads(path.read_text(encoding="utf-8"))["prompts"]["scoring"] == "我的评分标准"
    finally:
        controller.close()
        assert worker.wait(3000)


def test_close_flushes_pending_configuration(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    try:
        window.settings.cities.setText("深圳")
        window.close()
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=3000)
        path = tmp_path / "config" / "settings.json"
        assert json.loads(path.read_text(encoding="utf-8"))["cities"] == ["深圳"]
    finally:
        controller.close()
        worker.wait(3000)
