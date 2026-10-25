from types import SimpleNamespace
from unittest.mock import Mock

from boss_say_hello.domain import RunEvent


def waiting():
    return RunEvent(
        "r",
        "state",
        {
            "status": "paused",
            "requires_default_ack": True,
            "step": 1,
            "reason": "点击立即沟通可能先发送平台默认招呼语，然后发送生成的招呼语",
        },
    )


def test_log_explains_confirmation_without_diagnostic_fields(tmp_path, qtbot):
    from boss_say_hello.diagnostics import EventLog
    from boss_say_hello.ui.log_page import LogPage

    page = LogPage()
    qtbot.addWidget(page)
    log = EventLog(tmp_path, page.add_entry)
    try:
        log.event(waiting())
        text = page.output.toPlainText()
        assert "等待发送确认" in text and "默认招呼语" in text
        assert "继续" in text
    finally:
        log.close()


def test_confirmation_button_is_explicit_and_resets(qtbot):
    from boss_say_hello.ui.run_page import RunPage

    controller = SimpleNamespace(request_pause=Mock(), request_resume=Mock())
    page = RunPage(controller, lambda: None)
    qtbot.addWidget(page)
    page.on_event(RunEvent("r", "started", {"mode": "auto"}))
    page.on_event(waiting())
    assert "等待发送确认" in page.status.text()
    assert page.resume_button.text() == "已知晓，继续自动发送"
    assert page.resume_button.isEnabled()
    assert not page.pause_button.isEnabled()
    controller.request_resume.assert_not_called()
    page.idle()
    assert page.resume_button.text() == "继续本轮"


def test_confirmation_brings_run_tab_forward_without_acknowledging(qtbot):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.ui.main_window import MainWindow

    controller = Controller(WorkerThread())
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.on_event(RunEvent("r", "started", {"mode": "auto"}))
    window.tabs.setCurrentWidget(window.logs)
    window.on_event(waiting())
    assert window.tabs.currentWidget() is window.run
    assert not controller.control.default_message_acknowledged.is_set()
