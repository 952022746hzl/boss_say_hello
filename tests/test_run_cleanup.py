from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.domain import RunEvent, public_dict
from tests.test_agent_engine import environment


@pytest.mark.parametrize("provider_fails", [False, True])
async def test_stop_during_model_request_discards_late_result_without_error(
    config, snapshot, job, storage, tmp_path, provider_fails
):
    from boss_say_hello.agent.engine import AgentEngine
    from boss_say_hello.ai.schemas import AiFailure
    from boss_say_hello.diagnostics import EventLog

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)

    event_count = 0

    async def evaluate_job(*args, **kwargs):
        nonlocal event_count
        event_count = len(storage.events(ex.run.id))
        ex.control.closing.set()
        ex.control.pause_requested.set()
        if provider_fails:
            raise AiFailure("request_timeout")
        return {
            "tool_calls": [
                {"id": "late", "function": {"name": "inspect_preferences", "arguments": '{"reason":"late"}'}}
            ]
        }

    entries = []
    log = EventLog(tmp_path / "logs", entries.append)
    try:
        with log.bind():
            await AgentEngine(ex, SimpleNamespace(evaluate_job=evaluate_job)).run()
    finally:
        log.close()
    events = storage.events(ex.run.id)
    assert events[-1].payload["status"] == "stopped"
    assert not any(e.kind in {"tool_action", "tool_result", "error"} for e in events[event_count:])
    assert not any(e.kind == "state" and e.payload["status"] == "paused" for e in events)
    assert not any(e["level"] == "ERROR" for e in entries)
    assert browser.preference_snapshot.await_count == 1


def test_idle_clears_live_results_and_control_but_keeps_resume_and_config(qtbot, job, resume, config):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.ui.main_window import MainWindow

    worker = WorkerThread()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    controller.current_resume = resume()
    controller.resume_ready = True
    window.settings.set_config(config(target_title="Java", cities=("深圳",)))
    controller.receive(RunEvent("", "run_pending", {}))
    window.run.set_ready(True)
    controller.receive(RunEvent("r1", "started", {"mode": "preview"}))
    controller.receive(RunEvent("r1", "job", {"job_id": "j1", "job": public_dict(job())}))
    controller.receive(RunEvent("r1", "tool_action", {"tool": "inspect_preferences", "reason": "读取"}))
    old_control = controller.control
    controller.request_stop()
    assert not window.run.pause_button.isEnabled()
    controller.receive(RunEvent("r1", "finished", {"status": "stopped", "contacts": 0}))
    assert not window.run.start_button.isEnabled()  # Backend cleanup has not finished yet.
    controller.receive(RunEvent("", "run_idle", {}))
    assert window.run.run_id is None and not window.run.rows and not window.run.events
    assert window.run.table.rowCount() == 0
    assert not window.run.details.toPlainText() and not window.run.steps.toPlainText()
    assert window.run.start_button.isEnabled() and not window.run.stop_button.isEnabled()
    assert controller.control is not old_control and not controller.control.closing.is_set()
    assert controller.resume_ready and controller.current_resume.confirmed
    assert window.settings.read_config().target_title == "Java"
    controller.receive(RunEvent("r1", "job", {"job_id": "late", "job": public_dict(job())}))
    assert window.run.table.rowCount() == 0
    window.settings.dirty = False


async def test_backend_clears_browser_context_after_rejected_auto_run(tmp_path, config, resume):
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import BrowserCapabilities, SessionInfo
    from boss_say_hello.runtime.backend import Backend
    from boss_say_hello.runtime.control import RunControl

    browser = NodriverAdapter()
    browser.session = AsyncMock(return_value=SessionInfo("a1", True))
    browser.capabilities = AsyncMock(return_value=BrowserCapabilities(None, "unknown"))
    browser.bind_control(RunControl())
    browser.bind_goal(config())
    browser.preference_state = {"document": "old"}
    browser.candidate_seen.add("old")
    browser.active_job = object()
    backend = Backend(lambda e: None, root=tmp_path, browser=browser)
    await backend.initialize()
    try:
        with pytest.raises(ValueError, match="自动发送") as error:
            await backend.run(
                config(
                    target_title="Java", cities=("深圳",), salary_min=20000, salary_max=30000, auto_send=True
                ),
                resume(),
                RunControl(),
            )
        assert getattr(error.value, "code", None) == "send_capabilities_unknown"
        assert not backend.busy and browser.goal is None and browser.control is None
        assert browser.preference_state is None and not browser.candidate_seen
        assert browser.active_job is None
    finally:
        await backend.close()


def test_start_rejection_visible_on_run_page_and_logged_as_warning(
    qtbot, tmp_path, config, resume, monkeypatch
):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.ui.main_window import MainWindow
    from tests.fakes.browser import FakeBrowser

    browser = FakeBrowser()
    browser.behavior = "unknown"
    monkeypatch.setattr("boss_say_hello.runtime.backend.NodriverAdapter", lambda: browser)
    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    entries = []
    controller.event.connect(lambda e: entries.append(e.payload["entry"]) if e.kind == "log" else None)
    try:
        controller.current_resume = resume()
        controller.resume_ready = True
        controller.start_run(
            config(target_title="Java", cities=("深圳",), salary_min=20000, salary_max=30000, auto_send=True)
        )
        qtbot.waitUntil(lambda: not controller.run_pending)
        assert "关闭「自动发送」" in window.run.status.text()
        assert window.run.table.rowCount() == 0
        assert any(
            e["event"] == "run.blocked"
            and e["level"] == "WARNING"
            and e["fields"]["code"] == "send_capabilities_unknown"
            for e in entries
        )
        assert not any(e["level"] == "ERROR" for e in entries)
        assert not browser.calls
    finally:
        controller.close()
        assert worker.wait(3000)
