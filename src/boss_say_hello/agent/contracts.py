from dataclasses import dataclass

from boss_say_hello.ai.schemas import AiFailure, strings, text
from boss_say_hello.domain import Assessment


class EligibilityBlocked(ValueError):
    """Expected business outcome, not a model/network/program failure."""

    def __init__(self, job_id, result):
        super().__init__(result["reason"])
        self.job_id = job_id
        self.result = result


def validate_agent_goal(config):
    config.validate(ready=True)
    if not config.target_title.strip():
        raise ValueError("请填写期望职位")
    if len(config.cities) != 1:
        raise ValueError("自主运行每轮请选择一个工作城市")
    if config.salary_min is None or config.salary_max is None:
        raise ValueError("筛选岗位前请填写完整薪资区间")


@dataclass(frozen=True)
class Background:
    status: str
    score: int
    reason: str
    outsourcing: str
    source_ids: tuple[str, ...]


def background_decision(payload, evidence, job, config):
    try:
        refs = strings(payload["source_ids"])
        score = payload["score"]
        identity = payload["identity_verified"]
        employment = payload["employment_checked"]
        category = payload["outsourcing"]
        if (
            type(score) is not int
            or not 0 <= score <= 100
            or type(identity) is not bool
            or type(employment) is not bool
        ):
            raise AiFailure("invalid_background")
        if category not in {"clear", "suspected", "confirmed", "unknown"}:
            raise AiFailure("invalid_background")
        known = {s["id"] for s in evidence if s.get("text") and s.get("url")}
        if not set(refs) <= known:
            raise AiFailure("unknown_evidence", "背景结论引用了未收集的证据")
        status = "pass"
        if not identity or not refs:
            status = "pending"
        elif config.outsourcing_check:
            if category in {"suspected", "confirmed"}:
                status = "excluded"
            elif not employment or category == "unknown":
                status = "pending"
        return Background(status, score, text(payload["reason"]), category, refs)
    except (KeyError, TypeError):
        raise AiFailure("invalid_background") from None


def combined_assessment(assessment, background, config):
    if background.status != "pass":
        raise AiFailure("background_pending", "公司背景未通过，本岗位不能联系")
    weight = config.background_weight
    score = (assessment.score * (100 - weight) + background.score * weight + 50) // 100
    return Assessment(
        score,
        f"岗位 {assessment.score} 分 / 背景 {background.score} 分（背景权重 {weight}%）。{assessment.reason}；{background.reason}",
        assessment.evidence,
        assessment.gaps,
        assessment.missing,
    )
