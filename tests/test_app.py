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
