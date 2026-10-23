from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from boss_say_hello.ui.presentation import details


class HistoryPage(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        layout = QVBoxLayout(self)
        self.location = QLabel("岗位浏览、评分、招呼语及发送结果自动保存在本地工作目录")
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        refresh = QPushButton("刷新本地历史")
        refresh.clicked.connect(lambda: controller.load_history(None))
        layout.addWidget(refresh)
        split = QSplitter()
        self.runs = QListWidget()
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        split.addWidget(self.runs)
        split.addWidget(self.details)
        layout.addWidget(split)
        self.runs.currentItemChanged.connect(self.select)

    def set_runs(self, runs):
        self.runs.clear()
        for run in runs:
            item = QListWidgetItem(
                f"{run.created_at}\n{'自动发送' if run.config.auto_send else '预览'} · {run.id[:8]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, run.id)
            self.runs.addItem(item)
        if self.runs.count():
            self.runs.setCurrentRow(0)

    def select(self, current, previous=None):
        if current:
            self.controller.load_history(current.data(Qt.ItemDataRole.UserRole))

    def set_events(self, events):
        self.details.setPlainText(details(events))
