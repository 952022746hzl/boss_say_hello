from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QPushButton


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle('BOSS 求职助手')
        self.resize(1100, 760)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel('BOSS 求职助手 · 默认仅生成预览'))
        self.pause_button = QPushButton('暂停')
        self.pause_button.clicked.connect(controller.request_pause)
        layout.addWidget(self.pause_button)
        self.setCentralWidget(central)

    def closeEvent(self, event):
        self.controller.close()
        if self.controller.worker.isRunning():
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            event.accept()
