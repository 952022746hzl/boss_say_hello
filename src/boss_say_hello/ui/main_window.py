from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from boss_say_hello.ui.fields import mark_invalid
from boss_say_hello.ui.history_page import HistoryPage
from boss_say_hello.ui.log_page import LogPage
from boss_say_hello.ui.login_panel import LoginPanel
from boss_say_hello.ui.resume_page import ResumePage
from boss_say_hello.ui.run_page import RunPage
from boss_say_hello.ui.settings_page import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("BOSS 求职助手")
        self.resize(1100, 760)
        central = QWidget()
        layout = QVBoxLayout(central)
        heading = QLabel("BOSS 求职助手")
        heading.setStyleSheet("font-size: 24px; font-weight: 600; color: #103f46;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("推荐岗位 → 简历匹配 → AI 招呼语 · 每次启动默认预览"))
        self.login = LoginPanel(controller)
        layout.addWidget(self.login)
        self.settings = SettingsPage(controller)
        self.resume = ResumePage(controller)
        self.run = RunPage(controller, self.run_config)
        self.history = HistoryPage(controller)
        self.logs = LogPage()
        self.tabs = QTabWidget()
        for page, name in [
            (self.settings, "1 · 配置与提示词"),
            (self.resume, "2 · 简历确认"),
            (self.run, "3 · 岗位与运行"),
            (self.history, "4 · 本地历史"),
            (self.logs, "5 · 运行日志"),
        ]:
            self.tabs.addTab(page, name)
        layout.addWidget(self.tabs)
        self.pause_button = self.run.pause_button
        self.setCentralWidget(central)
        self.setStyleSheet(
            'QWidget {font-family: "Microsoft YaHei UI"; font-size: 13px;} QMainWindow {background: #f4f7f8;} QPushButton {padding: 7px 14px;} QLineEdit,QPlainTextEdit,QTableWidget,QListWidget {background: white; selection-background-color: #cae8e7;} QTabBar::tab {padding: 10px 16px;}'
        )
        controller.event.connect(self.on_event)

    def run_config(self):
        config = self.settings.read_config(ready=True)
        if self.settings.secret.text():
            self.settings.use_key()
        return config

    def on_event(self, event):
        p = event.payload
        if event.kind == "log":
            self.logs.add_entry(p["entry"])
        elif event.kind == "logs_ready":
            self.logs.set_location(p["path"], p["entries"])
        elif event.kind == "settings":
            self.settings.set_config(p["config"])
        elif event.kind == "resume":
            self.resume.set_version(p["version"])
            self.run.set_ready(p["version"].confirmed)
        elif event.kind == "resume_dirty":
            self.run.set_ready(False)
        elif event.kind == "session":
            self.login.update_session(p["session"])
        elif event.kind == "platform":
            self.settings.set_platform(p["caps"], p["attachments"])
        elif event.kind == "history_runs":
            self.history.set_runs(p["runs"])
        elif event.kind == "history_events":
            self.history.set_events(p["events"])
        elif event.kind in ("notice", "ui_error"):
            self.statusBar().showMessage(p["message"])
            self.settings.status.setText(p["message"])
            if p.get("code") == "missing_key":
                mark_invalid(self.settings.secret, True)
        elif event.kind == "run_pending":
            self.run.active = True
            self.run.set_ready(self.run.ready)
        elif event.kind == "run_idle":
            self.run.active = False
            self.run.set_ready(self.run.ready)
        else:
            self.run.on_event(event)

    def closeEvent(self, event):
        self.controller.close()
        if self.controller.worker.isRunning():
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            event.accept()
