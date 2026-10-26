from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from boss_say_hello.ui.presentation import LABELS, details


class RunPage(QWidget):
    def __init__(self, controller, config_reader):
        super().__init__()
        self.controller = controller
        self.config_reader = config_reader
        self.ready = False
        self.active = False
        self.stopping = False
        self.run_id = None
        self.rows = {}
        self.events = {}
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("开始本轮（使用当前配置）")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(controller.request_pause)
        self.resume_button = QPushButton("继续本轮")
        self.resume_button.clicked.connect(controller.request_resume)
        self.stop_button = QPushButton("结束本轮")
        self.stop_button.clicked.connect(lambda: controller.request_stop())
        for b in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self.status = QLabel("先登录 → 配置模型和规则 → 导入并确认简历 → 确认自动发送开关 → 开始本轮")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QLabel("成功发送 0；按岗位计数，失败和未知不计入成功目标")
        layout.addWidget(self.progress)
        self.steps = QPlainTextEdit()
        self.steps.setReadOnly(True)
        self.steps.setPlaceholderText("这里显示 AI 每一步的行动、简短理由和执行结果")
        self.steps.setMaximumHeight(160)
        self.steps.document().setMaximumBlockCount(1500)
        layout.addWidget(self.steps)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["公司", "岗位", "城市", "薪资", "匹配分", "状态"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.table.itemSelectionChanged.connect(self.show_selected)
        split.addWidget(self.table)
        split.addWidget(self.details)
        layout.addWidget(split, 1)
        self.set_ready(False)

    def set_ready(self, ready):
        self.ready = ready
        self.start_button.setEnabled(ready and not self.active)
        for button in (self.pause_button, self.resume_button, self.stop_button):
            button.setEnabled(self.active and not self.stopping)

    def clear_results(self):
        self.resume_button.setText("继续本轮")
        self.status.setStyleSheet("")
        self.run_id = None
        self.rows.clear()
        self.events.clear()
        self.table.setRowCount(0)
        self.details.clear()
        self.steps.clear()

    def pending(self):
        self.clear_results()
        self.active = True
        self.stopping = False
        self.set_ready(self.ready)
        self.status.setText("正在检查本轮配置并启动…")

    def idle(self):
        self.clear_results()
        self.active = False
        self.stopping = False
        self.set_ready(self.ready)

    def stopping_run(self):
        self.stopping = True
        self.set_ready(self.ready)
        self.status.setText("正在结束本轮，等待当前操作返回并保存历史…")

    def start(self):
        try:
            self.controller.start_run(self.config_reader())
        except ValueError as exc:
            self.status.setText(str(exc))

    def show_selected(self):
        row = self.table.currentRow()
        for id, index in self.rows.items():
            if index == row:
                self.details.setPlainText(details(self.events[id]))
                break

    def on_event(self, event):
        p = event.payload
        if event.kind == "started":
            self.clear_results()
            self.progress.setText("成功发送 0；本轮开始，按岗位计数")
            self.run_id = event.run_id
            self.active = True
            self.set_ready(self.ready)
            self.status.setText("自动发送运行中" if p["mode"] == "auto" else "预览运行中 · 不发送消息")
        elif event.run_id != self.run_id:
            return
        elif event.kind == "state":
            waiting_ack = bool(p.get("requires_default_ack"))
            self.resume_button.setText("已知晓，继续自动发送" if waiting_ack else "继续本轮")
            self.pause_button.setEnabled(self.active and not self.stopping and p["status"] != "paused")
            self.resume_button.setEnabled(self.active and not self.stopping and p["status"] == "paused")
            self.status.setStyleSheet("color: #975600; font-weight: bold;" if waiting_ack else "")
            reason = p.get("reason") or LABELS.get(p["status"], p["status"])
            self.status.setText(("等待发送确认：" if waiting_ack else "") + reason)
        elif event.kind == "finished":
            self.stopping = True
            self.set_ready(self.ready)
            self.status.setText(
                f"{p.get('reason') or LABELS.get(p['status'], p['status'])} · 联系尝试 {p['contacts']} · 记录可在本地历史查看"
            )
        if event.kind in {"progress", "finished"} and "successes" in p:
            self.progress.setText(
                f"成功发送 {p['successes']} / {p['contact_limit']} 个岗位 · 联系尝试 {p['contacts']} · 已发现 {p.get('discovered', 0)} · 已处理 {p.get('handled', 0)}"
            )
        if event.kind == "error":
            self.status.setText(p["reason"])
        if event.kind in {"tool_action", "tool_result"}:
            self.steps.appendPlainText(details([event]).strip())
        id = p.get("job_id")
        if id is None:
            return
        if id not in self.rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.rows[id] = row
            self.events[id] = []
            for col in range(6):
                self.table.setItem(row, col, QTableWidgetItem(""))
        row = self.rows[id]
        self.events[id].append(event)
        if event.kind == "job":
            j = p["job"]
            for col, key in enumerate(["company", "title", "city", "salary_raw"]):
                self.table.item(row, col).setText(j[key] or "待核实")
        if event.kind == "assessment":
            self.table.item(row, 4).setText(str(p["score"]))
        if "status" in p:
            self.table.item(row, 5).setText(LABELS.get(p["status"], p["status"]))
        if event.kind == "error":
            self.table.item(row, 5).setText("失败")
        if self.table.currentRow() < 0:
            self.table.selectRow(row)
        self.show_selected()
