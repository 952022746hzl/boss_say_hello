from PyQt6.QtCore import QObject, pyqtSignal
from boss_say_hello.runtime.control import RunControl


class Controller(QObject):
    event = pyqtSignal(object)

    def __init__(self, worker):
        super().__init__()
        self.worker = worker
        self.control = RunControl()
        worker.event.connect(self.event.emit)

    def request_pause(self):
        self.control.pause_requested.set()

    def request_resume(self):
        self.control.pause_requested.clear()

    def close(self):
        self.control.closing.set()
        self.control.pause_requested.set()
        self.worker.request_shutdown()
