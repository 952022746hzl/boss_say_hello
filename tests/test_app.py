def test_window_starts_without_configuration(qtbot):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.ui.main_window import MainWindow

    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    assert window.windowTitle() == "BOSS 求职助手"
    controller.close()
    assert worker.wait(3000)


def test_window_initializes_local_logs(qtbot, tmp_path, monkeypatch):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.ui.main_window import MainWindow

    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    try:
        controller.load_settings()
        qtbot.waitUntil(lambda: window.logs.open_button.isEnabled(), timeout=3000)
        qtbot.waitUntil(lambda: "后台服务已就绪" in window.logs.output.toPlainText(), timeout=3000)
        assert (tmp_path / "logs" / "application.log").exists()
        controller.unhandled(ValueError("sensitive-value"))
        assert controller.control.pause_requested.is_set()
    finally:
        controller.close()
        assert worker.wait(3000)
    assert "sensitive-value" not in (tmp_path / "logs" / "application.log").read_text(encoding="utf-8")
