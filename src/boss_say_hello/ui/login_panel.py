from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel


class LoginPanel(QWidget):
    def __init__(self, controller):
        super().__init__()
        layout = QHBoxLayout(self)
        self.status = QLabel('未登录 · 使用独立 Chrome 登录窗口')
        open_button = QPushButton('打开 BOSS 登录')
        check_button = QPushButton('检查登录状态')
        open_button.clicked.connect(controller.open_login)
        check_button.clicked.connect(controller.check_login)
        layout.addWidget(self.status, 1); layout.addWidget(open_button); layout.addWidget(check_button)

    def update_session(self, session):
        self.status.setText(f'已登录 · 账号 {session.account_id}' if session.logged_in and session.account_id else '未登录 / 账号标识待核实')
