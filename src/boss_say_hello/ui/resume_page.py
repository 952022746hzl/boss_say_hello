from pathlib import Path

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from boss_say_hello.ui.fields import mark_invalid, required_label, watch_field


class ResumePage(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.version = None
        self.setStyleSheet('QPlainTextEdit[invalid="true"] {border: 1px solid #c62828; background: #fff5f5;}')
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(required_label("PDF 简历"))
        upload = QPushButton("导入 PDF 简历")
        upload.clicked.connect(self.choose_file)
        row.addWidget(upload)
        self.status = QLabel("尚未导入 · 仅支持有文本的 PDF")
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setPlaceholderText("PDF 原文，用于核对")
        self.keywords = QPlainTextEdit()
        self.keywords.setPlaceholderText("关键词，每行一个")
        self.keywords.setMaximumHeight(150)
        self.summary = QPlainTextEdit()
        self.summary.setPlaceholderText("可修改的真实经历摘要")
        editor = QWidget()
        fields = QVBoxLayout(editor)
        fields.addWidget(required_label("关键词"))
        fields.addWidget(self.keywords)
        fields.addWidget(required_label("经历摘要"))
        fields.addWidget(self.summary)
        split = QSplitter()
        split.addWidget(self.original)
        split.addWidget(editor)
        layout.addWidget(split, 1)
        self.confirm_button = QPushButton("确认使用本版本")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.confirm)
        layout.addWidget(self.confirm_button)
        self.keywords.textChanged.connect(self.invalidate)
        self.summary.textChanged.connect(self.invalidate)
        watch_field(self.keywords, self.keywords.textChanged)
        watch_field(self.summary, self.summary.textChanged)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择简历", "", "PDF (*.pdf)")
        if path:
            self.invalidate()
            self.confirm_button.setEnabled(False)
            self.controller.import_resume(Path(path))

    def invalidate(self):
        self.status.setText("未确认 · 修改完成后请确认使用")
        self.controller.invalidate_resume()

    def set_version(self, version):
        self.version = version
        blockers = [QSignalBlocker(self.keywords), QSignalBlocker(self.summary)]
        self.original.setPlainText(version.original_text)
        self.keywords.setPlainText("\n".join(version.keywords))
        self.summary.setPlainText(version.summary)
        self.status.setText(
            "已确认 · 新任务使用此版本" if version.confirmed else "未确认 · 请核对关键词和摘要"
        )
        self.confirm_button.setEnabled(True)
        del blockers

    def confirm(self):
        if self.version:
            missing = []
            for field, name in ((self.keywords, "关键词"), (self.summary, "经历摘要")):
                empty = not field.toPlainText().strip()
                mark_invalid(field, empty)
                if empty:
                    missing.append(name)
            if missing:
                self.status.setText("请填写必填项：" + "、".join(missing))
                return
            self.controller.confirm_resume(
                self.version, tuple(self.keywords.toPlainText().splitlines()), self.summary.toPlainText()
            )
