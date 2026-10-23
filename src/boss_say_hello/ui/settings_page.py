from dataclasses import replace
import re
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,
    QLabel,QPushButton,QSpinBox,QDoubleSpinBox,QCheckBox,QComboBox,QTabWidget,QScrollArea)
from boss_say_hello.domain import AppConfig,PromptSet


class SettingsPage(QWidget):
    def __init__(self,controller):
        super().__init__()
        self.controller=controller; self.config=AppConfig()
        root=QVBoxLayout(self); scroll=QScrollArea(); scroll.setWidgetResizable(True)
        body=QWidget(); layout=QVBoxLayout(body); form=QFormLayout()
        self.cities=QLineEdit(); self.cities.setPlaceholderText('北京, 上海；留空不限城市')
        self.salary_min=QLineEdit(); self.salary_max=QLineEdit()
        self.salary_min.setPlaceholderText('税前月薪下限，元'); self.salary_max.setPlaceholderText('上限；可留空')
        salary=QWidget(); row=QHBoxLayout(salary); row.setContentsMargins(0,0,0,0)
        row.addWidget(self.salary_min); row.addWidget(QLabel('至')); row.addWidget(self.salary_max)
        form.addRow('城市',self.cities); form.addRow('薪资区间',salary)
        self.base_url=QLineEdit(); self.base_url.setPlaceholderText('https://服务地址/v1')
        self.model=QLineEdit(); self.model.setPlaceholderText('服务提供的模型名称')
        self.secret=QLineEdit(); self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText('留空使用本次会话密钥或系统凭据')
        form.addRow('API 地址',self.base_url); form.addRow('模型名称',self.model); form.addRow('API 密钥',self.secret)
        layout.addLayout(form)
        keys=QHBoxLayout()
        for label,fn in [('连接测试',self.test),('仅本次会话使用',self.use_key),('保存到系统凭据',self.save_key)]:
            b=QPushButton(label); b.clicked.connect(fn); keys.addWidget(b)
        layout.addLayout(keys)
        hint=QLabel('PDF 提炼会把原文发送至此 API；评分和招呼语使用你确认的摘要、关键词及岗位内容。密钥不写入配置文件。')
        hint.setWordWrap(True); layout.addWidget(hint)
        tabs=QTabWidget(); self.prompts=[]
        for name in ['求职偏好','评分标准','招呼语风格']:
            edit=QPlainTextEdit(); edit.setMinimumHeight(135); self.prompts.append(edit); tabs.addTab(edit,name)
        layout.addWidget(tabs)
        layout.addWidget(QLabel('明确配置优先于提示词。修改配置只影响下一轮任务。'))
        limits=QFormLayout(); self.min_score=QSpinBox(); self.min_score.setRange(0,100)
        self.max_contacts=QSpinBox(); self.max_contacts.setRange(1,10000)
        self.interval=QDoubleSpinBox(); self.interval.setRange(.1,86400); self.interval.setSuffix(' 秒')
        limits.addRow('最低匹配分',self.min_score); limits.addRow('每轮联系上限',self.max_contacts); limits.addRow('联系间隔',self.interval)
        self.auto_send=QCheckBox('自动发送（点击开始后按本轮快照执行）')
        self.send_attachment=QCheckBox('额外发送已选的平台简历附件')
        self.attachment=QComboBox(); self.attachment.addItem('未选择平台附件',None)
        limits.addRow(self.auto_send); limits.addRow(self.send_attachment); limits.addRow('平台附件',self.attachment)
        layout.addLayout(limits)
        self.capabilities=QLabel('平台发送能力尚未核实；仍可生成预览。附件不会从本地 PDF 自动上传。')
        self.capabilities.setWordWrap(True); layout.addWidget(self.capabilities)
        self.status=QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        save=QPushButton('保存配置与三个提示词'); save.clicked.connect(self.save); layout.addWidget(save)
        scroll.setWidget(body); root.addWidget(scroll); self.set_config(self.config)

    def set_config(self,config):
        self.config=config
        self.cities.setText(', '.join(config.cities))
        self.salary_min.setText('' if config.salary_min is None else str(config.salary_min))
        self.salary_max.setText('' if config.salary_max is None else str(config.salary_max))
        self.base_url.setText(config.base_url); self.model.setText(config.model)
        for edit,text in zip(self.prompts,(config.prompts.preference,config.prompts.scoring,config.prompts.greeting)): edit.setPlainText(text)
        self.min_score.setValue(config.min_score); self.max_contacts.setValue(config.max_contacts); self.interval.setValue(config.interval_seconds)
        self.auto_send.setChecked(config.auto_send); self.send_attachment.setChecked(config.send_attachment)
        self.attachment.setCurrentIndex(max(0,self.attachment.findData(config.attachment_id)))

    def read_config(self):
        try:
            low=int(self.salary_min.text()) if self.salary_min.text().strip() else None
            high=int(self.salary_max.text()) if self.salary_max.text().strip() else None
        except ValueError:
            raise ValueError('薪资请填写整数金额，或留空') from None
        result=replace(self.config,cities=tuple(x for x in re.split(r'[,，;；\s]+',self.cities.text().strip()) if x),
            salary_min=low,salary_max=high,base_url=self.base_url.text().strip(),model=self.model.text().strip(),
            prompts=PromptSet(*(p.toPlainText().strip() for p in self.prompts)),min_score=self.min_score.value(),
            max_contacts=self.max_contacts.value(),interval_seconds=self.interval.value(),auto_send=self.auto_send.isChecked(),
            send_attachment=self.send_attachment.isChecked(),attachment_id=self.attachment.currentData())
        result.validate(); return result

    def save(self):
        try: self.controller.save_settings(self.read_config())
        except ValueError as exc: self.status.setText(str(exc))

    def test(self):
        try: self.controller.test_connection(self.read_config(),self.secret.text())
        except ValueError as exc: self.status.setText(str(exc))

    def use_key(self):
        self.controller.use_credential(self.config.credential_id,self.secret.text()); self.secret.clear()

    def save_key(self):
        self.controller.save_credential(self.config.credential_id,self.secret.text()); self.secret.clear()

    def set_platform(self,caps,attachments):
        selected=self.attachment.currentData() or self.config.attachment_id
        self.attachment.clear(); self.attachment.addItem('未选择平台附件',None)
        for a in attachments: self.attachment.addItem(a.name,a.id)
        self.attachment.setCurrentIndex(max(0,self.attachment.findData(selected)))
        behavior={'none':'未发现默认招呼语','default_message':'首次联系会发送平台默认招呼语','unknown':'首次联系行为未核实'}
        self.capabilities.setText(f"{behavior.get(caps.initial_contact_behavior,'行为未核实')}；消息长度限制：{caps.greeting_limit or '待核实'}。未知时自动发送会被阻止。")
