from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class LoginPanel(QWidget):
    def __init__(self, controller):
        super().__init__()
        layout = QHBoxLayout(self)
        self.status = QLabel("未登录 · 使用独立 Chrome 登录窗口")
        open_button = QPushButton("打开 BOSS 登录")
        check_button = QPushButton("检查登录状态")
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(controller.check_login)
        open_button.clicked.connect(lambda: (controller.open_login(), self.timer.start()))
        check_button.clicked.connect(controller.check_login)
        layout.addWidget(self.status, 1)
        layout.addWidget(open_button)
        layout.addWidget(check_button)

    def update_session(self, session):
        ready = session.logged_in and session.account_id
        if ready:
            self.timer.stop()
        self.status.setText(
            f"已登录 · 账号 {session.account_id[:8]}… · 请配置模型并确认简历"
            if ready
            else "等待登录 / 账号标识待核实"
        )
