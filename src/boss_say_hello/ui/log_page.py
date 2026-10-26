import json
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class LogPage(QWidget):
    def __init__(self):
        super().__init__()
        self.entries = deque(maxlen=1000)
        self.path = None
        self.requests = {}
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
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.output)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(QLabel("完整 AI 请求正文（本地文件）"))
        self.request_selector = QComboBox()
        self.request_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.request_selector.setMinimumContentsLength(24)
        self.request_selector.currentIndexChanged.connect(self.show_request)
        detail_layout.addWidget(self.request_selector)
        self.request_location = QLabel("尚无完整请求日志")
        self.request_location.setWordWrap(True)
        self.request_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.request_location)
        self.request_details = QPlainTextEdit()
        self.request_details.setReadOnly(True)
        detail_layout.addWidget(self.request_details)
        splitter.addWidget(detail)
        layout.addWidget(splitter)
        self.level.currentTextChanged.connect(self.render)
        layout.addWidget(
            QLabel(
                "进度日志仅保留元数据；完整请求含简历、岗位和提示词，保存在本地 ai_requests；排除密钥与 Cookie。\n"
                "完整请求最多保留 500 个、合计 100 MB；正在处理的请求保留至完成。"
            )
        )

    def set_location(self, path, entries):
        self.path = str(path)
        self.open_button.setEnabled(True)
        self.location.setText(f"本地日志：{self.path} · 5 MB / 文件，最多 5 个备份")
        self.entries.clear()
        self.entries.extend(entries)
        self.requests.clear()
        self.request_selector.clear()
        for entry in self.entries:
            self._add_request(entry)
        self.render()

    def add_entry(self, entry):
        self.entries.append(entry)
        self._add_request(entry)
        self.render()

    def _add_request(self, entry):
        fields = entry.get("fields", {})
        request_id = fields.get("request_id")
        path = fields.get("request_path")
        if not request_id or not path:
            return
        if request_id not in self.requests:
            self.requests[request_id] = path
            self.request_selector.addItem(
                f"{entry['time']} · {fields.get('stage', 'request')} · {request_id}",
                request_id,
            )
            if len(self.requests) > 500:
                oldest = self.request_selector.itemData(0)
                self.requests.pop(oldest, None)
                self.request_selector.removeItem(0)
        if self.request_selector.currentData() == request_id:
            self.show_request()

    def show_request(self):
        path = self.requests.get(self.request_selector.currentData())
        if not path:
            self.request_location.setText("尚无完整请求日志")
            self.request_details.clear()
            return
        path = Path(path)
        if not path.is_absolute() and self.path:
            path = Path(self.path) / path
        self.request_location.setText(f"完整请求文件：{path}")
        try:
            self.request_details.setPlainText(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            self.request_details.setPlainText("无法读取完整请求日志：文件可能已清理，或本地目录不可访问。")

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
        self.requests.clear()
        self.request_selector.clear()
        self.show_request()

    def open_folder(self):
        if self.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))
