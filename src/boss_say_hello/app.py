import sys
from PyQt6.QtWidgets import QApplication
from boss_say_hello.runtime.worker import WorkerThread
from boss_say_hello.runtime.controller import Controller
from boss_say_hello.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('BossSayHello')
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    window.show()
    controller.load_settings()
    try:
        return app.exec()
    finally:
        controller.close()
        worker.wait()
