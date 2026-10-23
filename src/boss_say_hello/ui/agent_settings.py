from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QWidget

from boss_say_hello.ui.fields import required_label, watch_field


class AgentSettings(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        self.target_title = QLineEdit()
        self.target_title.setPlaceholderText("如 Java；与平台职位分类名称一致")
        form.addRow(required_label("期望职位（开始本轮必填）"), self.target_title)
        watch_field(self.target_title, self.target_title.textChanged)
        self.target_title.textChanged.connect(self.changed.emit)
        self.outsourcing_check = QCheckBox("排除有外包、驻场、派遣嫌疑的公司或岗位（默认开启）")
        self.outsourcing_check.toggled.connect(self.changed.emit)
        form.addRow(self.outsourcing_check)
        note = QLabel(
            "AI 会核对平台期望和筛选、查询公司来源并评分。无法核实时停止或标为待确认。公司查询目前使用 Moonshot 官方搜索服务。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.filters = {}
        options = {
            "薪资": ["3K以下", "3-5K", "5-10K", "10-20K", "20-50K", "50K以上"],
            "经验": [
                "不限",
                "在校生",
                "应届生",
                "经验不限",
                "1年以内",
                "1-3年",
                "3-5年",
                "5-10年",
                "10年以上",
            ],
            "学历": ["不限", "初中及以下", "中专中技", "高中", "大专", "本科", "硕士", "博士"],
            "公司规模": ["不限", "0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
            "行业": ["不限"],
            "融资阶段": [
                "不限",
                "未融资",
                "天使轮",
                "A轮",
                "B轮",
                "C轮",
                "D轮及以上",
                "已上市",
                "不需要融资",
            ],
        }
        for name, choices in options.items():
            field = QComboBox()
            field.setEditable(True)
            field.addItems((["自动按薪资区间选档"] if name == "薪资" else ["未指定"]) + choices)
            field.setToolTip("可填写页面上的准确选项名称；页面不存在时不会擅自替换")
            field.currentTextChanged.connect(self.changed.emit)
            self.filters[name] = field
            form.addRow(f"BOSS 原生{name}", field)
        self.limits = {}
        for key, label, low, high, suffix in (
            ("background_weight", "公司背景权重", 0, 100, " %"),
            ("max_candidates", "每轮候选上限", 1, 1000, " 个"),
            ("max_agent_steps", "每轮 AI 决策上限", 1, 3000, " 步"),
            ("max_job_steps", "单岗位决策上限", 1, 100, " 步"),
            ("max_company_searches", "每公司联网查询上限", 1, 20, " 次"),
            ("company_cache_days", "公司资料缓存有效期", 1, 30, " 天"),
        ):
            field = QSpinBox()
            field.setRange(low, high)
            field.setSuffix(suffix)
            field.valueChanged.connect(self.changed.emit)
            self.limits[key] = field
            form.addRow(required_label(label), field)

    def load(self, config):
        self.target_title.setText(config.target_title)
        self.outsourcing_check.setChecked(config.outsourcing_check)
        for key, field in self.limits.items():
            field.setValue(getattr(config, key))
        for key, field in self.filters.items():
            field.setCurrentText(config.native_filters.get(key, field.itemText(0)))

    def values(self):
        return {
            "target_title": self.target_title.text().strip(),
            "outsourcing_check": self.outsourcing_check.isChecked(),
            "native_filters": {
                k: f.currentText().strip()
                for k, f in self.filters.items()
                if f.currentText().strip() not in {"", f.itemText(0)}
            },
            **{k: f.value() for k, f in self.limits.items()},
        }
