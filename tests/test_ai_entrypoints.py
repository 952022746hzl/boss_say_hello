import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from boss_say_hello.domain import RunEvent
from boss_say_hello.runtime.backend import Backend
from boss_say_hello.runtime.control import RunControl
from boss_say_hello.runtime.controller import Controller
from boss_say_hello.runtime.worker import WorkerThread
from tests.fakes.browser import FakeBrowser


@pytest.mark.parametrize("active", ["run", "import_resume", "test_connection"])
@pytest.mark.parametrize("requested", ["run", "import_resume", "test_connection"])
def test_controller_rejects_overlapping_ai_entrypoints(qtbot, config, resume, active, requested):
    controller = Controller(WorkerThread())
    controller.submit = Mock()
    controller.current_resume = resume()
    controller.resume_ready = True
    cfg = config()
    actions = {
        "run": lambda: controller.start_run(cfg),
        "import_resume": lambda: controller.import_resume("synthetic.pdf", cfg, "fake"),
        "test_connection": lambda: controller.test_connection(cfg, "fake"),
    }
    actions[active]()
    with pytest.raises(ValueError):
        actions[requested]()


@pytest.mark.parametrize("active", ["run", "import_resume", "test_connection"])
@pytest.mark.parametrize("requested", ["run", "import_resume", "test_connection"])
async def test_backend_rejects_overlapping_ai_entrypoints(
    tmp_path, config, resume, monkeypatch, active, requested
):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def operation(*args):
        entered.set()
        await release.wait()
        return resume(confirmed=False)

    # Slow external boundaries are synthetic, while the real backend owns admission and cleanup.
    ai = SimpleNamespace(test_connection=operation, close=AsyncMock())
    monkeypatch.setattr("boss_say_hello.runtime.backend.AiService", lambda *args: ai)
    monkeypatch.setattr("boss_say_hello.runtime.backend.AgentClient", lambda *args: ai)
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.CompanySearch", lambda *args: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr("boss_say_hello.runtime.backend.ResumeService.import_pdf", operation)
    monkeypatch.setattr("boss_say_hello.runtime.backend.ToolExecutor", Mock())
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.AgentEngine", lambda *args: SimpleNamespace(run=operation)
    )
    backend = Backend(lambda e: None, root=tmp_path, browser=FakeBrowser())
    await backend.initialize()
    backend.secret = AsyncMock(return_value="fake")
    backend.credentials.save = Mock()
    cfg = config(target_title="Java", cities=("深圳",), salary_min=20000, salary_max=30000)
    actions = {
        "run": lambda: backend.run(cfg, resume(), RunControl()),
        "import_resume": lambda: backend.import_resume("synthetic.pdf", 1, cfg, "fake"),
        "test_connection": lambda: backend.test_connection(cfg, "fake"),
    }
    running = asyncio.create_task(actions[active]())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(ValueError):
            await asyncio.wait_for(actions[requested](), 0.05)
        # Configuration saving and history reading remain available while AI work is active.
        await backend.save_settings(cfg)
        await backend.load_history(None)
    finally:
        release.set()
        await running
        await backend.close()


@pytest.mark.parametrize("active", ["run", "import_resume", "test_connection"])
def test_window_disables_competing_ai_buttons_and_restores_them(qtbot, config, resume, active):
    from boss_say_hello.ui.main_window import MainWindow

    controller = Controller(WorkerThread())
    controller.submit = Mock()
    controller.current_resume = resume()
    controller.resume_ready = True
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.settings.set_config(config())
    window.resume.set_version(resume())
    window.run.set_ready(True)
    actions = {
        "run": lambda: controller.start_run(config()),
        "import_resume": lambda: controller.import_resume("synthetic.pdf", config(), "fake"),
        "test_connection": lambda: controller.test_connection(config(), "fake"),
    }
    actions[active]()
    assert not window.settings.test_button.isEnabled()
    assert not window.resume.upload_button.isEnabled()
    assert not window.run.start_button.isEnabled()
    assert window.settings.base_url.isEnabled()
    assert window.history.isEnabled()
    if active == "test_connection":
        assert "测试中" in window.settings.test_button.text()
    controller.receive(RunEvent("", "ui_error", {"operation": active, "message": "合成失败"}))
    idle = {"run": "run_idle", "import_resume": "resume_import_idle", "test_connection": "connection_test_idle"}
    controller.receive(RunEvent("", idle[active], {}))
    assert window.settings.test_button.isEnabled()
    assert window.settings.test_button.text() == "连接测试并保存"
    assert window.resume.upload_button.isEnabled()
    assert window.run.start_button.isEnabled() is (active != "import_resume")
    assert "合成失败" in window.settings.status.text()


@pytest.mark.parametrize("action", ["run", "import_resume", "test_connection"])
def test_failed_worker_submission_restores_pending_state_and_buttons(qtbot, config, resume, action):
    from boss_say_hello.ui.main_window import MainWindow

    worker = WorkerThread()

    def unavailable(coro):
        coro.close()
        raise RuntimeError("后台服务尚未就绪或正在关闭")

    worker.submit = unavailable
    controller = Controller(worker)
    controller.current_resume = resume()
    controller.resume_ready = True
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.run.set_ready(True)
    actions = {
        "run": lambda: controller.start_run(config()),
        "import_resume": lambda: controller.import_resume("synthetic.pdf", config(), "fake"),
        "test_connection": lambda: controller.test_connection(config(), "fake"),
    }
    with pytest.raises(ValueError, match="后台"):
        actions[action]()
    assert not controller.run_pending and not controller.import_pending and not controller.test_pending
    assert window.settings.test_button.isEnabled()
    assert window.resume.upload_button.isEnabled()
    assert "后台" in window.settings.status.text()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_connection_idle_restores_buttons_after_every_outcome(qtbot, tmp_path, monkeypatch, config, outcome):
    from boss_say_hello.ai.schemas import AiFailure
    from boss_say_hello.ui.main_window import MainWindow

    entered = threading.Event()
    release = threading.Event()

    async def test_connection():
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        if outcome == "failure":
            raise AiFailure("authentication_failed", "合成密钥失败")

    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    monkeypatch.setattr(
        "boss_say_hello.runtime.backend.AiService",
        lambda *args: SimpleNamespace(test_connection=test_connection, close=AsyncMock()),
    )
    monkeypatch.setattr("keyring.set_password", lambda *args: None)
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    try:
        future = controller.test_connection(config(), "synthetic-key")
        qtbot.waitUntil(entered.is_set, timeout=3000)
        assert not window.settings.test_button.isEnabled()
        assert controller.test_pending
        if outcome == "cancelled":
            future.cancel()
        else:
            release.set()
        qtbot.waitUntil(lambda: not controller.test_pending, timeout=3000)
        assert window.settings.test_button.isEnabled()
        assert window.resume.upload_button.isEnabled()
        assert window.settings.test_button.text() == "连接测试并保存"
        if outcome == "failure":
            assert window.settings.status.text() == "合成密钥失败"
        if outcome == "success":
            assert "连接测试成功" in window.settings.status.text()
        assert controller.backend.ai_operation is None
    finally:
        release.set()
        controller.close()
        assert worker.wait(3000)
