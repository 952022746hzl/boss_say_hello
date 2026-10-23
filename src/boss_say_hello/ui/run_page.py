from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QTableWidget,QTableWidgetItem,QPlainTextEdit,QSplitter,QAbstractItemView,QHeaderView
from PyQt6.QtCore import Qt
from boss_say_hello.ui.presentation import LABELS,details


class RunPage(QWidget):
    def __init__(self,controller,config_reader):
        super().__init__(); self.controller=controller; self.config_reader=config_reader
        self.ready=False; self.active=False; self.run_id=None; self.rows={}; self.events={}
        layout=QVBoxLayout(self); buttons=QHBoxLayout()
        self.start_button=QPushButton('开始本轮（使用当前配置）'); self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.pause_button=QPushButton('暂停'); self.pause_button.clicked.connect(controller.request_pause)
        self.resume_button=QPushButton('继续本轮'); self.resume_button.clicked.connect(controller.request_resume)
        for b in (self.start_button,self.pause_button,self.resume_button): buttons.addWidget(b)
        layout.addLayout(buttons); self.status=QLabel('先登录 → 导入并确认简历 → 配置模型和规则 → 开始预览')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        split=QSplitter(Qt.Orientation.Vertical)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(['公司','岗位','城市','薪资','匹配分','状态'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.details=QPlainTextEdit(); self.details.setReadOnly(True)
        self.table.itemSelectionChanged.connect(self.show_selected)
        split.addWidget(self.table); split.addWidget(self.details); layout.addWidget(split,1)

    def set_ready(self,ready):
        self.ready=ready; self.start_button.setEnabled(ready and not self.active)

    def start(self):
        try: self.controller.start_run(self.config_reader())
        except ValueError as exc: self.status.setText(str(exc))

    def show_selected(self):
        row=self.table.currentRow()
        for id,index in self.rows.items():
            if index==row: self.details.setPlainText(details(self.events[id])); break

    def on_event(self,event):
        p=event.payload
        if event.kind=='started':
            self.run_id=event.run_id; self.active=True; self.set_ready(self.ready)
            self.rows.clear(); self.events.clear(); self.table.setRowCount(0); self.details.clear()
            self.status.setText('自动发送运行中' if p['mode']=='auto' else '预览运行中 · 不发送消息')
        elif event.run_id != self.run_id: return
        elif event.kind=='state': self.status.setText(LABELS.get(p['status'],p['status']))
        elif event.kind=='finished':
            self.active=False; self.set_ready(self.ready)
            self.status.setText(f"{LABELS.get(p['status'],p['status'])} · 本轮联系/未知占额 {p['contacts']}")
        if event.kind=='error': self.status.setText(p['reason'])
        id=p.get('job_id')
        if id is None: return
        if id not in self.rows:
            row=self.table.rowCount(); self.table.insertRow(row); self.rows[id]=row; self.events[id]=[]
            for col in range(6): self.table.setItem(row,col,QTableWidgetItem(''))
        row=self.rows[id]; self.events[id].append(event)
        if event.kind=='job':
            j=p['job']
            for col,key in enumerate(['company','title','city','salary_raw']): self.table.item(row,col).setText(j[key] or '待核实')
        if event.kind=='assessment': self.table.item(row,4).setText(str(p['score']))
        if 'status' in p: self.table.item(row,5).setText(LABELS.get(p['status'],p['status']))
        if event.kind=='error': self.table.item(row,5).setText('失败')
        if self.table.currentRow()<0: self.table.selectRow(row)
        self.show_selected()
