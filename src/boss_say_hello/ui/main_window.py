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
        self.tag_account = None
        self.ai_operations = set()
        self.setWindowTitle("BOSS 求职助手")
        self.resize(1100, 760)
        central = QWidget()
        layout = QVBoxLayout(central)
        heading = QLabel("BOSS 求职助手")
        heading.setStyleSheet("font-size: 24px; font-weight: 600; color: #103f46;")
        layout.addWidget(heading)
        layout.addWidget(
            QLabel("岗位筛选 · AI 综合评估与招呼语 · 点击开始后执行，发送方式以配置为准")
        )
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
        pending = {
            "run_pending": "run",
            "resume_import_pending": "import_resume",
            "connection_test_pending": "test_connection",
        }
        idle = {
            "run_idle": "run",
            "resume_import_idle": "import_resume",
            "connection_test_idle": "test_connection",
        }
        if event.kind in pending:
            self.ai_operations.add(pending[event.kind])
        elif event.kind in idle:
            self.ai_operations.discard(idle[event.kind])
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
        elif event.kind == "connection_test_pending":
            self.settings.status.setText("正在测试模型连接，请稍候…")
        elif event.kind == "connection_test_idle":
            pass
        elif event.kind == "resume_confirm_pending":
            self.resume.set_confirming(True)
        elif event.kind == "resume_confirm_idle":
            self.resume.set_confirming(False)
        elif event.kind == "resume_draft":
            self.resume.restore_draft(p["draft"])
            self.run.set_ready(False)
        elif event.kind == "resume_draft_saved":
            self.resume.acknowledge_save(p["generation"])
        elif event.kind == "session":
            self.login.update_session(p["session"])
            account = p["session"].account_id if p["session"].logged_in else None
            if account != self.tag_account or account is None:
                self.tag_account = account
                self.settings.agent.set_expectations([])
        elif event.kind == "expectations":
            if p["account_id"] == self.tag_account:
                self.settings.agent.set_expectations(p["items"])
        elif event.kind == "platform":
            self.settings.set_platform(p["caps"])
        elif event.kind == "history_runs":
            self.history.set_runs(p["runs"])
        elif event.kind == "history_events":
            self.history.set_events(p["events"])
        elif event.kind in ("notice", "ui_error"):
            self.statusBar().showMessage(p["message"])
            self.settings.status.setText(p["message"])
            if event.kind == "ui_error" and p.get("operation") == "run":
                self.run.status.setText(p["message"])
            if event.kind == "ui_error" and p.get("operation") in ("confirm_resume", "import_resume"):
                self.resume.status.setText(p["message"])
            if p.get("code") in ("missing_key", "invalid_key", "authentication_failed"):
                mark_invalid(self.settings.secret, True)
        elif event.kind == "run_pending":
            self.settings.status.clear()
            self.statusBar().clearMessage()
            self.settings.agent.refresh_tags.setEnabled(False)
            self.settings.agent.target_title.setEnabled(False)
            self.run.pending()
        elif event.kind == "run_stopping":
            self.run.stopping_run()
        elif event.kind == "run_idle":
            self.settings.agent.refresh_tags.setEnabled(True)
            self.settings.agent.target_title.setEnabled(True)
            self.run.idle()
        else:
            self.run.on_event(event)
            if event.kind == "state" and p.get("requires_default_ack"):
                self.tabs.setCurrentWidget(self.run)
                self.statusBar().showMessage(
                    "任务等待发送确认，请阅读提示并点击「已知晓，继续自动发送」；尚未开始岗位处理"
                )
        ai_busy = bool(self.ai_operations)
        self.settings.set_ai_busy(ai_busy, testing="test_connection" in self.ai_operations)
        self.resume.set_ai_busy(ai_busy)
        self.run.start_button.setEnabled(self.run.ready and not self.run.active and not ai_busy)

    def closeEvent(self, event):
        if not self.controller.shutting_down:
            if self.controller.confirm_pending:
                event.ignore()
                QTimer.singleShot(100, self.close)
                return
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
