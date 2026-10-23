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
        self.close_save_futures = None
        self.setWindowTitle("BOSS 求职助手")
        self.resize(1100, 760)
        central = QWidget()
        layout = QVBoxLayout(central)
        heading = QLabel("BOSS 求职助手")
        heading.setStyleSheet("font-size: 24px; font-weight: 600; color: #103f46;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("AI 自主筛选 · 公司背景核查 · 简历匹配与招呼语 · 每次启动默认预览"))
        self.login = LoginPanel(controller)
        layout.addWidget(self.login)
        self.settings = SettingsPage(controller)
        self.resume = ResumePage(controller, self.resume_config)
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

    def resume_config(self):
        try:
            config = self.settings.read_config(ready=True)
            return config, self.settings.read_secret(config)
        except ValueError as exc:
            self.tabs.setCurrentWidget(self.settings)
            self.settings.status.setText(str(exc))
            raise

    def run_config(self):
        try:
            config = self.settings.read_run_config()
        except ValueError as exc:
            self.tabs.setCurrentWidget(self.settings)
            self.settings.status.setText(str(exc))
            raise
        if self.settings.secret.text():
            if not self.settings.use_key():
                raise ValueError(self.settings.status.text())
        return config

    def on_event(self, event):
        p = event.payload
        if event.kind == "log":
            self.logs.add_entry(p["entry"])
        elif event.kind == "logs_ready":
            self.logs.set_location(p["path"], p["entries"])
        elif event.kind == "settings":
            self.settings.set_config(p["config"])
        elif event.kind == "settings_saved":
            self.settings.acknowledge_save(p["config"])
        elif event.kind == "workspace":
            self.settings.set_workspace(p["path"])
            self.history.location.setText(f"岗位记录文件：{p['path'] / 'history' / 'jobs.json'}")
        elif event.kind == "credential":
            self.settings.restore_secret(p["scope"], p["secret"])
        elif event.kind == "resume":
            self.resume.set_version(p["version"])
            self.run.set_ready(p["version"].confirmed)
        elif event.kind == "resume_dirty":
            self.run.set_ready(False)
        elif event.kind == "resume_import_pending":
            self.resume.set_importing(True)
        elif event.kind == "resume_import_idle":
            self.resume.set_importing(False)
        elif event.kind == "resume_draft":
            self.resume.restore_draft(p["draft"])
            self.run.set_ready(False)
        elif event.kind == "resume_draft_saved":
            self.resume.acknowledge_save(p["generation"])
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
            if p.get("code") in ("missing_key", "invalid_key", "authentication_failed"):
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
        if not self.controller.control.closing.is_set():
            if self.close_save_futures is None and (self.settings.dirty or self.resume.dirty):
                try:
                    self.close_save_futures = [
                        future
                        for future in (self.settings.flush_save(), self.resume.flush_save())
                        if future is not None
                    ]
                    self.settings.setEnabled(False)
                    self.resume.setEnabled(False)
                except ValueError as exc:
                    self.tabs.setCurrentWidget(self.settings)
                    self.settings.status.setText(f"关闭前保存失败：{exc}")
                    event.ignore()
                    return
            if self.close_save_futures is not None:
                if not all(future.done() for future in self.close_save_futures):
                    event.ignore()
                    QTimer.singleShot(100, self.close)
                    return
                if any(future.result() is False for future in self.close_save_futures):
                    self.close_save_futures = None
                    self.settings.setEnabled(True)
                    self.resume.setEnabled(True)
                    self.settings.status.setText("配置保存失败，请检查工作目录权限后重试关闭")
                    event.ignore()
                    return
        self.controller.close()
        if self.controller.worker.isRunning():
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            event.accept()
