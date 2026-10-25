import json
from collections import deque

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogPage(QWidget):
    def __init__(self):
        super().__init__()
        self.entries = deque(maxlen=1000)
        self.path = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.level = QComboBox()
        self.level.addItems(["进度与异常", "全部", "DEBUG", "INFO", "WARNING", "ERROR"])
        row.addWidget(QLabel("日志级别"))
        row.addWidget(self.level)
        self.follow = QCheckBox("自动滚动")
        self.follow.setChecked(True)
        row.addWidget(self.follow)
        self.show_fields = QCheckBox("显示诊断字段")
        self.show_fields.toggled.connect(self.render)
        row.addWidget(self.show_fields)
        clear = QPushButton("清空当前显示")
        clear.clicked.connect(self.clear_view)
        row.addWidget(clear)
        self.open_button = QPushButton("打开本地日志目录")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_folder)
        row.addWidget(self.open_button)
        row.addStretch()
        layout.addLayout(row)
        self.location = QLabel("日志仅保存在本地：每个文件 5 MB，最多保留 5 个备份。")
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(1000)
        layout.addWidget(self.output)
        self.level.currentTextChanged.connect(self.render)
        layout.addWidget(
            QLabel("记录阶段、状态、耗时与错误位置；不记录密钥、Cookie、简历/岗位正文或招呼语内容。")
        )

    def set_location(self, path, entries):
        self.path = str(path)
        self.open_button.setEnabled(True)
        self.location.setText(f"本地日志：{self.path} · 5 MB / 文件，最多 5 个备份")
        self.entries.clear()
        self.entries.extend(entries)
        self.render()

    def add_entry(self, entry):
        self.entries.append(entry)
        self.render()

    def render(self):
        level = self.level.currentText()
        previous = self.output.verticalScrollBar().value()
        lines = [
            self.format_entry(e)
            for e in self.entries
            if level == "全部" or e["level"] == level or (level == "进度与异常" and e["level"] != "DEBUG")
        ]
        self.output.setPlainText("\n".join(lines))
        self.output.verticalScrollBar().setValue(
            self.output.verticalScrollBar().maximum() if self.follow.isChecked() else previous
        )

    def format_entry(self, entry):
        text = f"{entry['time']} [{entry['level']}] {entry.get('message', entry['event'])}"
        fields = entry.get("fields", {})
        if self.show_fields.isChecked():
            return text + " " + json.dumps(fields, ensure_ascii=False)
        if "duration_ms" in fields:
            text += f" · 耗时 {fields['duration_ms'] / 1000:.2f} 秒"
        if fields.get("code"):
            text += " · 原因代码：" + fields["code"]
        elif fields.get("error_type"):
            text += " · " + fields["error_type"]
        return text

    def clear_view(self):
        self.entries.clear()
        self.output.clear()

    def open_folder(self):
        if self.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))
