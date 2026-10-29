"""Conservative company identity and Beijing calendar boundaries for delivery quotas."""

import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlsplit

from boss_say_hello.domain import Job

BEIJING = timezone(timedelta(hours=8))
_COMPANY_ID = re.compile(r"[A-Za-z0-9_~-]{1,128}")
_COMPANY_PATH = re.compile(r"/gongsi/([A-Za-z0-9_~-]{1,128})\.html")
_LEGAL_SUFFIX = re.compile(
    r"(?:有限公司|有限责任公司|股份公司|合伙企业(?:[（(][^()（）]+[）)])?|"
    r"\b(?:Limited|Ltd\.?|Inc\.?|LLC|Corporation))$",
    re.IGNORECASE,
)


def company_full_name(job: Job) -> str:
    """Keep exact full names; do not collapse similar brands or different legal entities."""
    name = " ".join(job.company_full_name.split())
    if not name:
        candidate = " ".join(job.company.split())
        name = candidate if _LEGAL_SUFFIX.search(candidate) else ""
    if name in {"未知", "待确认", "暂无", "公司名称", "-", "--"} or "…" in name or "..." in name:
        return ""
    return name


def company_identity(job: Job) -> str | None:
    """Prefer platform IDs, then IDs from strictly verified platform company URLs."""
    company_id = job.company_id.strip()
    if _COMPANY_ID.fullmatch(company_id):
        return f"id:{company_id}"
    try:
        url = urlsplit(job.company_url)
        match = _COMPANY_PATH.fullmatch(url.path)
        if (
            url.scheme in {"http", "https"}
            and url.hostname in {"www.zhipin.com", "zhipin.com"}
            and url.username is None
            and url.password is None
            and url.port in {None, 443 if url.scheme == "https" else 80}
            and match
        ):
            return f"id:{match[1]}"
    except ValueError:
        pass
    name = company_full_name(job)
    return f"name:{name}" if name else None


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("配额时钟必须包含时区")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def beijing_day_bounds(value: datetime) -> tuple[str, str]:
    if value.tzinfo is None:
        raise ValueError("配额时钟必须包含时区")
    start = datetime.combine(value.astimezone(BEIJING).date(), time.min, tzinfo=BEIJING)
    return utc_timestamp(start), utc_timestamp(start + timedelta(days=1))
