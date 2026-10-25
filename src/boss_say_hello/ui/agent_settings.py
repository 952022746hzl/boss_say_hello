from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from boss_say_hello.ui.fields import required_label, watch_field


class AgentSettings(QWidget):
    changed = pyqtSignal()
    city_selected = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        self.saved_target = ("", "")
        self.target_title = QComboBox()
        self.target_title.setPlaceholderText("登录后读取，请选择已有求职标签")
        self.refresh_tags = QPushButton("刷新标签")
        self.refresh_tags.clicked.connect(self.refresh_requested.emit)
        tags_row = QHBoxLayout()
        tags_row.addWidget(self.target_title, 1)
        tags_row.addWidget(self.refresh_tags)
        form.addRow(required_label("BOSS 已有求职标签"), tags_row)
        watch_field(self.target_title, self.target_title.currentIndexChanged)
        self.target_title.currentIndexChanged.connect(self.select_expectation)
        self.outsourcing_check = QCheckBox("排除有外包、驻场、派遣嫌疑的公司或岗位（默认开启）")
        self.outsourcing_check.toggled.connect(self.changed.emit)
        form.addRow(self.outsourcing_check)
        note = QLabel(
            "登录后自动读取已有标签，选择后带出职位和城市；修改 BOSS 期望后可刷新。选中标签后直接读取岗位，薪资和偏好在本地评估。不自动新增标签。公司背景合并为一次查询，证据不足保留待确认；使用 Moonshot 官方搜索服务。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.limits = {}
        for key, label, low, high, suffix in (
            ("background_weight", "公司背景权重", 0, 100, " %"),
            ("max_candidates", "每轮候选上限", 1, 1000, " 个"),
            ("max_agent_steps", "每轮 AI 决策上限", 1, 3000, " 步"),
            ("max_job_steps", "单岗位决策上限", 1, 100, " 步"),
            ("company_cache_days", "公司资料缓存有效期", 1, 30, " 天"),
        ):
            field = QSpinBox()
            field.setRange(low, high)
            field.setSuffix(suffix)
            field.valueChanged.connect(self.changed.emit)
            self.limits[key] = field
            form.addRow(required_label(label), field)

    def load(self, config):
        self.saved_target = (config.target_title, config.cities[0] if len(config.cities) == 1 else "")
        self.restore_target()
        self.outsourcing_check.setChecked(config.outsourcing_check)
        for key, field in self.limits.items():
            field.setValue(getattr(config, key))

    def restore_target(self):
        self.target_title.blockSignals(True)
        index = -1
        for i in range(self.target_title.count()):
            item = self.target_title.itemData(i)
            if (item["title"], item["city"]) == self.saved_target:
                index = i
                break
        title, city = self.saved_target
        self.target_title.setPlaceholderText(
            f"上次选择：{title}（{city}），请登录读取或重新选择"
            if title
            else "登录后读取，请选择已有求职标签"
        )
        self.target_title.setCurrentIndex(index)
        self.target_title.blockSignals(False)

    def set_expectations(self, items):
        self.target_title.blockSignals(True)
        self.target_title.clear()
        for item in items:
            self.target_title.addItem(item["label"], item)
        self.restore_target()

    def select_expectation(self):
        item = self.target_title.currentData()
        if item:
            self.saved_target = (item["title"], item["city"])
            self.city_selected.emit(item["city"])
            self.changed.emit()

    def values(self):
        return {
            "target_title": self.saved_target[0],
            "outsourcing_check": self.outsourcing_check.isChecked(),
            **{k: f.value() for k, f in self.limits.items()},
        }
