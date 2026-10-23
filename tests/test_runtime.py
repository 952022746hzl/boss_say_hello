import asyncio
import threading


def test_worker_uses_one_event_loop(qtbot):
    from boss_say_hello.runtime.worker import WorkerThread
    worker = WorkerThread()
    worker.start()
    async def get_loop(): return id(asyncio.get_running_loop())
    first = worker.submit(get_loop()).result(3)
    assert worker.submit(get_loop()).result(3) == first
    worker.request_shutdown()
    assert worker.wait(3000)


def test_window_responsive_during_background_wait(qtbot):
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.ui.main_window import MainWindow
    from PyQt6.QtCore import Qt
    worker = WorkerThread(); worker.start()
    controller = Controller(worker)
    window = MainWindow(controller); qtbot.addWidget(window)
    window.show()
    started = threading.Event()
    async def blocked():
        started.set()
        await asyncio.sleep(100)
    future = worker.submit(blocked())
    assert started.wait(2)
    qtbot.mouseClick(window.pause_button, Qt.MouseButton.LeftButton)
    assert controller.control.pause_requested.is_set()
    assert not future.done()
    controller.close()
    assert worker.wait(3000)
    assert future.done()
