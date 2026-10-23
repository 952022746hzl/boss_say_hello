"""Immutable records passed between UI and background services."""

from dataclasses import asdict, dataclass, field
from math import isfinite
from urllib.parse import urlparse


@dataclass(frozen=True)
class PromptSet:
    preference: str = "优先选择与候选人真实经历、技能和发展方向相关的岗位。"
    scoring: str = "综合技能、项目经验和职责匹配程度打分（0到100）。说明依据、缺口和信息不足，不虚构事实。"
    greeting: str = (
        "写80到150个中文字，口语、自然、简短。只说1到2个真实且相关的经历，不编造年限或成果，不含联系方式。"
    )


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
    auto_send: bool = False
    min_score: int = 70
    max_contacts: int = 20
    interval_seconds: float = 30.0
    send_attachment: bool = False
    attachment_id: str | None = None

    def validate(self, *, ready: bool = False) -> None:
        for value in (self.salary_min, self.salary_max):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("薪资必须为非负整数或留空")
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("薪资下限不能超过上限")
        if type(self.min_score) is not int or not 0 <= self.min_score <= 100:
            raise ValueError("匹配分必须在0到100之间")
        if type(self.max_contacts) is not int or self.max_contacts < 1:
            raise ValueError("每轮上限必须为正整数")
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
        if self.auto_send and self.send_attachment and not self.attachment_id:
            raise ValueError("请先选择平台简历附件")


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
