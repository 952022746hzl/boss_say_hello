"""Immutable records passed between UI and background services."""

from dataclasses import asdict, dataclass, field
from math import isfinite
from urllib.parse import urlparse


@dataclass(frozen=True)
class PromptSet:
    preference: str = """你是我的求职筛选助手，请结合已确认简历和当前岗位判断匹配度。
1. 以界面配置的城市、薪资等硬条件为准；与本提示词冲突时，明确配置优先。
2. 优先考虑职责与我真实技能、项目经验相关，能够发挥已有优势并有合理成长空间的岗位。
3. 不凭岗位名称或关键词重合就认定匹配；关注实际工作内容、必备技能、经验和学历要求。
4. 简历或岗位没有写明的信息标为待确认；不要推断我接受异地、外包、出差、加班等条件。
5. 如果我补充了行业、公司规模、工作方式或排除项，请说明岗位是否满足以及证据来源。
可在这里补充我的个人偏好："""
    scoring: str = """按百分制评分，只使用已确认简历和岗位片段中的事实。
评分维度：核心技能匹配 40 分；相关项目与工作经历 30 分；岗位职责匹配 20 分；明确任职条件 10 分。
每项按证据充分程度计分后相加，总分为 0 到 100 的整数，不因关键词重复额外加分。
90—100：核心要求高度匹配，证据充分；75—89：主要要求匹配，有少量缺口；
60—74：部分匹配，有明显缺口或关键信息不足；0—59：核心要求缺乏支持。
缺失信息不能当成已满足，也不要把未提及的经历直接说成没有。
理由中简述各维度得分、主要匹配依据、明确差距和待确认问题。
不虚构年限、学历、项目规模或成果；硬条件是否通过由程序独立判断，分数不能推翻硬条件。"""
    greeting: str = """为当前岗位写一段自然、礼貌、简短的中文招呼语，建议 80—150 字，并遵守平台长度限制。
结构：简短问候 + 点明关注的岗位或职责 + 1—2 个有简历依据的相关技能/经历 + 表达沟通意愿。
只使用已确认简历中的真实信息，结合本岗位具体要求；优先选与岗位最相关的经历。
不虚构工作年限、量化成果、管理经验或已使用的技术，不保证能胜任所有要求。
不要堆砌技能、机械复述招聘描述、夸大评价、使用表情或附带联系方式。
信息不足时减少具体断言；不输出占位符，不把评分理由或个人短板写进招呼语。
只返回规定格式中的招呼语正文及对应简历依据，不额外解释。"""


MAX_JOB_DECISIONS = 12


@dataclass(frozen=True)
class AppConfig:
    revision: int = 0
    cities: tuple[str, ...] = ()
    salary_min: int | None = None
    salary_max: int | None = None
    prompts: PromptSet = field(default_factory=PromptSet)
    base_url: str = ""
    model: str = ""
    credential_id: str = "default-model"
    auto_send: bool = True
    min_score: int = 70
    max_contacts: int = 20
    max_company_daily_contacts: int = 1
    interval_seconds: float = 30.0
    model_timeout_seconds: int = 180
    target_title: str = ""
    outsourcing_check: bool = True
    background_weight: int = 20

    def validate(self, *, ready: bool = False) -> None:
        if not isinstance(self.target_title, str) or len(self.target_title) > 100:
            raise ValueError("期望职位须为不超过 100 字的文本")
        if type(self.outsourcing_check) is not bool:
            raise ValueError("外包检查开关无效")
        if type(self.background_weight) is not int or not 0 <= self.background_weight <= 100:
            raise ValueError("背景权重必须为 0 到 100 的整数")
        if type(self.model_timeout_seconds) is not int or not 10 <= self.model_timeout_seconds <= 1800:
            raise ValueError("模型等待时间必须为 10 到 1800 秒之间的整数")
        for value in (self.salary_min, self.salary_max):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("薪资必须为非负整数或留空")
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("薪资下限不能超过上限")
        if type(self.min_score) is not int or not 0 <= self.min_score <= 100:
            raise ValueError("匹配分必须在0到100之间")
        if type(self.max_contacts) is not int or self.max_contacts < 1:
            raise ValueError("每轮成功发送目标必须为正整数")
        if (
            type(self.max_company_daily_contacts) is not int
            or not 1 <= self.max_company_daily_contacts <= 10000
        ):
            raise ValueError("同公司每日成功岗位上限必须为 1 到 10000 的整数")
        if (
            isinstance(self.interval_seconds, bool)
            or not isfinite(self.interval_seconds)
            or self.interval_seconds <= 0
        ):
            raise ValueError("发送间隔必须为正数")
        if self.base_url:
            parsed = urlparse(self.base_url)
            if (
                parsed.scheme not in ("https", "http")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("API 地址须为 http/https 地址，不能包含凭据或查询参数")
        if ready and (not self.base_url or not self.model.strip()):
            raise ValueError("请填写 API 地址和模型名")


@dataclass(frozen=True)
class ResumeVersion:
    id: str
    pdf_hash: str
    original_text: str
    keywords: tuple[str, ...]
    summary: str
    confirmed: bool


@dataclass(frozen=True)
class Job:
    account_id: str
    id: str
    recruiter_id: str | None
    title: str
    company: str
    city: str | None
    salary_raw: str
    text: str
    url: str
    contact_status: str = "unknown"
    company_id: str = ""
    company_full_name: str = ""
    company_url: str = ""
    recruiter_name: str = ""


@dataclass(frozen=True)
class CheckResult:
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assessment:
    score: int
    reason: str
    evidence: tuple[str, ...]
    gaps: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class Greeting:
    text: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunSnapshot:
    id: str
    account_id: str
    config: AppConfig
    resume: ResumeVersion
    created_at: str


@dataclass(frozen=True)
class Attempt:
    id: str
    run_id: str
    job_id: str
    stage: str
    outcome: str


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    kind: str
    payload: dict


def config_from_dict(data: dict) -> AppConfig:
    data = dict(data)
    data.pop("max_company_searches", None)
    data.pop("native_filters", None)  # Retire saved native filters without losing other settings.
    data["prompts"] = PromptSet(**data.get("prompts", {}))
    data["cities"] = tuple(data.get("cities", ()))
    config = AppConfig(**data)
    config.validate()
    return config


def resume_from_dict(data: dict) -> ResumeVersion:
    return ResumeVersion(**{**data, "keywords": tuple(data["keywords"])})


def snapshot_from_dict(data: dict) -> RunSnapshot:
    return RunSnapshot(
        **{**data, "config": config_from_dict(data["config"]), "resume": resume_from_dict(data["resume"])}
    )


def public_dict(value) -> dict:
    return asdict(value)
