import re
from dataclasses import replace

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from boss_say_hello.domain import AppConfig, PromptSet
from boss_say_hello.storage.credentials import credential_scope
from boss_say_hello.ui.agent_settings import AgentSettings
from boss_say_hello.ui.fields import mark_invalid, required_label, visibility_icon, watch_field


class SettingsPage(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.config = AppConfig()
        self.loading = False
        self.dirty = False
        self.workspace_path = None
        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.setSingleShot(True)
        self.auto_save_timer.setInterval(600)
        self.auto_save_timer.timeout.connect(self.save)
        self.setStyleSheet(
            'QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"], QComboBox[invalid="true"] {border: 1px solid #c62828; background: #fff5f5;}'
        )
        root = QVBoxLayout(self)
        workspace_row = QHBoxLayout()
        self.workspace_label = QLabel("本地工作目录：加载中")
        self.workspace_label.setWordWrap(True)
        self.open_workspace = QPushButton("打开工作目录")
        self.open_workspace.setEnabled(False)
        self.open_workspace.clicked.connect(self.open_workspace_folder)
        workspace_row.addWidget(self.workspace_label, 1)
        workspace_row.addWidget(self.open_workspace)
        root.addLayout(workspace_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        form = QFormLayout()
        self.cities = QLineEdit()
        self.cities.setPlaceholderText("每轮一个城市，如 深圳")
        self.salary_min = QLineEdit()
        self.salary_max = QLineEdit()
        self.salary_min.setPlaceholderText("税前月薪下限，元")
        self.salary_max.setPlaceholderText("税前月薪上限，元")
        salary = QWidget()
        row = QHBoxLayout(salary)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.salary_min)
        row.addWidget(QLabel("至"))
        row.addWidget(self.salary_max)
        form.addRow(required_label("城市（开始本轮必填）"), self.cities)
        form.addRow(required_label("薪资区间（开始本轮必填）"), salary)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://服务地址/v1")
        self.model = QLineEdit()
        self.model.setPlaceholderText("服务提供的模型名称")
        self.model_timeout = QSpinBox()
        self.model_timeout.setRange(10, 1800)
        self.model_timeout.setSuffix(" 秒")
        self.model_timeout.setToolTip(
            "等待模型响应数据的超时时间，默认 180 秒；连接超时为 15 秒。修改在下次操作生效。"
        )
        self.secret = QLineEdit()
        self.secret_scope = None
        self.secret.textChanged.connect(self.bind_entered_secret)
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("填写后测试连接；成功后自动保存，下次启动恢复")
        secret_row = QWidget()
        secret_layout = QHBoxLayout(secret_row)
        secret_layout.setContentsMargins(0, 0, 0, 0)
        secret_layout.addWidget(self.secret)
        self.secret_visibility = QToolButton()
        self.secret_visibility.setCheckable(True)
        self.secret_visibility.setFixedSize(32, 30)
        self.secret_visibility.toggled.connect(self.set_secret_visible)
        self.set_secret_visible(False)
        secret_layout.addWidget(self.secret_visibility)
        form.addRow(required_label("API 地址"), self.base_url)
        form.addRow(required_label("模型名称"), self.model)
        form.addRow(required_label("模型等待时间"), self.model_timeout)
        form.addRow(required_label("API 密钥（未保存时）"), secret_row)
        for field in (self.base_url, self.model, self.secret, self.salary_min, self.salary_max):
            watch_field(field, field.textChanged)
        layout.addLayout(form)
        keys = QHBoxLayout()
        self.test_button = QPushButton("连接测试并保存")
        self.test_button.clicked.connect(self.test)
        keys.addWidget(self.test_button)
        layout.addLayout(keys)
        hint = QLabel(
            "PDF 提炼会把原文发送至此 API；评分和招呼语使用你确认的摘要、关键词及岗位内容。密钥不写入配置文件。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.agent = AgentSettings()
        layout.addWidget(self.agent)
        self.agent.changed.connect(self.schedule_save)
        self.agent.city_selected.connect(self.cities.setText)
        self.agent.refresh_requested.connect(lambda: self.controller.refresh_expectations())
        tabs = self.prompt_tabs = QTabWidget()
        self.prompts = []
        for name in ["求职偏好", "评分标准", "招呼语风格"]:
            edit = QPlainTextEdit()
            edit.setMinimumHeight(135)
            self.prompts.append(edit)
            tabs.addTab(edit, name)
        layout.addWidget(tabs)
        defaults = QPushButton("恢复当前提示词的默认模板")
        defaults.clicked.connect(self.restore_current_prompt)
        layout.addWidget(defaults)
        layout.addWidget(QLabel("配置与提示词修改后自动保存。明确配置优先；修改只影响下一次操作。"))
        limits = QFormLayout()
        self.min_score = QSpinBox()
        self.min_score.setRange(0, 100)
        self.max_contacts = QSpinBox()
        self.max_contacts.setRange(1, 10000)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 86400)
        self.interval.setSuffix(" 秒")
        limits.addRow(required_label("最低匹配分"), self.min_score)
        limits.addRow(required_label("每轮成功发送目标（岗位数）"), self.max_contacts)
        limits.addRow(required_label("联系间隔"), self.interval)
        self.auto_send = QCheckBox("自动发送（点击开始后按本轮快照执行）")
        limits.addRow(self.auto_send)
        layout.addLayout(limits)
        self.capabilities = QLabel("平台发送能力尚未核实；仍可生成预览。")
        self.capabilities.setWordWrap(True)
        layout.addWidget(self.capabilities)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        save = QPushButton("立即保存配置与提示词")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        scroll.setWidget(body)
        root.addWidget(scroll)
        self.set_config(self.config)
        for field in (self.cities, self.salary_min, self.salary_max, self.base_url, self.model):
            field.textChanged.connect(self.schedule_save)
        for field in self.prompts:
            field.textChanged.connect(self.schedule_save)
        for field in (self.min_score, self.max_contacts, self.interval, self.model_timeout):
            field.valueChanged.connect(self.schedule_save)
        self.auto_send.toggled.connect(self.schedule_save)

    def set_workspace(self, path):
        self.workspace_path = path
        self.workspace_label.setText(f"本地工作目录：{path}")
        self.open_workspace.setEnabled(True)

    def open_workspace_folder(self):
        if self.workspace_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace_path)))

    def restore_current_prompt(self):
        defaults = PromptSet()
        texts = (defaults.preference, defaults.scoring, defaults.greeting)
        index = self.prompt_tabs.currentIndex()
        self.prompts[index].setPlainText(texts[index])

    def schedule_save(self):
        if not self.loading:
            self.dirty = True
            self.auto_save_timer.start()

    def acknowledge_save(self, config):
        self.config = replace(self.config, revision=max(self.config.revision, config.revision))
        try:
            if replace(self.read_config(), revision=config.revision) == config:
                self.dirty = False
        except ValueError:
            pass

    def flush_save(self):
        self.auto_save_timer.stop()
        return self.controller.save_settings(self.read_config()) if self.dirty else None

    def restore_secret(self, scope, secret):
        if not self.secret.text() and self.base_url.text().strip():
            try:
                if credential_scope(self.read_config()) == scope:
                    self.secret.setText(secret)
                    self.secret_scope = scope
            except ValueError:
                pass

    def set_secret_visible(self, visible):
        self.secret.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        label = "隐藏密钥" if visible else "显示密钥"
        self.secret_visibility.setToolTip(label)
        self.secret_visibility.setAccessibleName(label)
        self.secret_visibility.setIcon(visibility_icon(visible))

    def set_config(self, config):
        self.loading = True
        self.auto_save_timer.stop()
        self.config = config
        self.agent.load(config)
        self.cities.setText(", ".join(config.cities))
        self.salary_min.setText("" if config.salary_min is None else str(config.salary_min))
        self.salary_max.setText("" if config.salary_max is None else str(config.salary_max))
        self.base_url.setText(config.base_url)
        self.model.setText(config.model)
        self.model_timeout.setValue(config.model_timeout_seconds)
        for edit, text in zip(
            self.prompts,
            (config.prompts.preference, config.prompts.scoring, config.prompts.greeting),
            strict=True,
        ):
            edit.setPlainText(text)
        self.min_score.setValue(config.min_score)
        self.max_contacts.setValue(config.max_contacts)
        self.interval.setValue(config.interval_seconds)
        self.auto_send.setChecked(config.auto_send)
        self.loading = False
        self.dirty = False

    def read_config(self, ready=False):
        if ready:
            missing = []
            for field, name in ((self.base_url, "API 地址"), (self.model, "模型名称")):
                empty = not field.text().strip()
                mark_invalid(field, empty)
                if empty:
                    missing.append(name)
            if missing:
                raise ValueError("请填写必填项：" + "、".join(missing))
        try:
            low = int(self.salary_min.text()) if self.salary_min.text().strip() else None
            high = int(self.salary_max.text()) if self.salary_max.text().strip() else None
        except ValueError:
            mark_invalid(self.salary_min, True)
            mark_invalid(self.salary_max, True)
            raise ValueError("薪资请填写整数金额，或留空") from None
        if low is not None and high is not None and low > high:
            mark_invalid(self.salary_min, True)
            mark_invalid(self.salary_max, True)
            raise ValueError("薪资下限不能超过上限")
        result = replace(
            self.config,
            cities=tuple(x for x in re.split(r"[,，;；\s]+", self.cities.text().strip()) if x),
            salary_min=low,
            salary_max=high,
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            model_timeout_seconds=self.model_timeout.value(),
            prompts=PromptSet(*(p.toPlainText().strip() for p in self.prompts)),
            min_score=self.min_score.value(),
            max_contacts=self.max_contacts.value(),
            interval_seconds=self.interval.value(),
            auto_send=self.auto_send.isChecked(),
            **self.agent.values(),
        )
        result.validate(ready=ready)
        return result

    def read_run_config(self):
        from boss_say_hello.agent.contracts import validate_agent_goal

        result = self.read_config(ready=True)
        selected = self.agent.target_title.currentData()
        invalid_tag = not selected or (
            selected["title"] != result.target_title or (selected["city"],) != result.cities
        )
        for field, invalid in (
            (self.agent.target_title, invalid_tag),
            (self.cities, len(result.cities) != 1),
            (self.salary_min, result.salary_min is None),
            (self.salary_max, result.salary_max is None),
        ):
            mark_invalid(field, invalid)
        if invalid_tag:
            raise ValueError("请登录并从下拉框选择已有求职标签；城市需与所选标签一致")
        validate_agent_goal(result)
        return result

    def save(self):
        try:
            self.auto_save_timer.stop()
            return self.controller.save_settings(self.read_config())
        except ValueError as exc:
            self.status.setText(str(exc))

    def test(self):
        try:
            self.auto_save_timer.stop()
            config = self.read_config(ready=True)
            self.controller.test_connection(config, self.read_secret(config))
        except ValueError as exc:
            self.status.setText(str(exc))

    def bind_entered_secret(self):
        self.secret_scope = None
        if self.secret.text():
            try:
                self.secret_scope = credential_scope(
                    replace(self.config, base_url=self.base_url.text().strip())
                )
            except ValueError:
                self.secret_scope = "unbound"

    def read_secret(self, config):
        secret = self.secret.text()
        if secret:
            scope = credential_scope(config)
            if self.secret_scope != scope:
                mark_invalid(self.secret, True)
                raise ValueError("API 地址已变化或输入密钥时尚未填写有效地址，请重新输入该服务的密钥")
        return secret

    def use_key(self):
        try:
            config = self.read_config()
            credential_scope(config)
            self.controller.use_credential(config, self.read_secret(config))
            return True
        except ValueError as exc:
            self.status.setText(str(exc))
            return False

    def save_key(self):
        try:
            config = self.read_config()
            credential_scope(config)
            self.controller.save_credential(config, self.read_secret(config))
        except ValueError as exc:
            self.status.setText(str(exc))

    def set_platform(self, caps):
        behavior = {
            "none": "未发现默认招呼语",
            "default_message": "首次联系会发送平台默认招呼语",
            "may_default_message": "首次联系可能先发送默认招呼语，运行时需确认",
            "unknown": "首次联系行为未核实",
        }
        limit_label = (
            "应用招呼语上限（发送时再核验编辑器）" if caps.limit_source == "application" else "消息长度上限"
        )
        self.capabilities.setText(
            f"{behavior.get(caps.initial_contact_behavior, '行为未核实')}；{limit_label}：{caps.greeting_limit or '待核实'}。未知时自动发送会被阻止。"
        )
