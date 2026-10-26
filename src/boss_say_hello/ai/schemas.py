import json
import math

from boss_say_hello.diagnostics import record
from boss_say_hello.domain import Assessment, Greeting


class AiFailure(RuntimeError):
    PROVIDER_LIMIT_KINDS = {
        "rate_limit_reached": "temporary",
        "rate_limit_reached_error": "temporary",
        "rate_limit_exceeded": "temporary",
        "requests_per_minute_limit_exceeded": "temporary",
        "tokens_per_minute_limit_exceeded": "temporary",
        "concurrency_limit_exceeded": "temporary",
        "engine_overloaded": "temporary",
        "engine_overloaded_error": "temporary",
        "insufficient_quota": "quota",
        "insufficient_balance": "quota",
        "balance_insufficient": "quota",
        "exceeded_current_quota_error": "quota",
        "billing_hard_limit_reached": "quota",
        "credit_balance_too_low": "quota",
        "daily_limit_exceeded": "daily",
        "tokens_per_day_limit_exceeded": "daily",
        "requests_per_day_limit_exceeded": "daily",
    }
    DIAGNOSTIC_CODES = {
        "request_timeout",
        "connection_failed",
        "bad_request",
        "authentication_failed",
        "permission_denied",
        "not_found",
        "rate_limited",
        "quota_exhausted",
        "daily_limit",
        "service_unavailable",
        "http_error",
        "request_failed",
        "missing_key",
        "invalid_key",
        "response_truncated",
        "response_filtered",
        "empty_keywords",
        "invalid_fields",
        "greeting_too_long",
        "context_too_large",
        "invalid_decision",
        "search_failed",
        "invalid_json",
        "empty_content",
        "invalid_score",
        "invalid_background",
        "unknown_evidence",
        "background_pending",
    }

    def __init__(
        self,
        code: str,
        public_message: str = "模型返回内容无效，请检查配置或重新评估",
        *,
        http_status=None,
        provider_code=None,
        limit_kind=None,
        retry_after_seconds=None,
        can_retry=False,
    ):
        self.code = code
        self.public_message = public_message
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.provider_code = (
            provider_code
            if isinstance(provider_code, str) and provider_code in self.PROVIDER_LIMIT_KINDS
            else None
        )
        self.limit_kind = limit_kind if limit_kind in ("temporary", "quota", "daily", "unknown") else None
        self.retry_after_seconds = None
        if type(retry_after_seconds) in (int, float) and retry_after_seconds >= 0:
            try:
                if math.isfinite(retry_after_seconds):
                    self.retry_after_seconds = retry_after_seconds
            except OverflowError:
                pass
        self.can_retry = can_retry is True
        super().__init__(public_message)

    @property
    def is_limit(self):
        return self.http_status == 429 or self.code in ("rate_limited", "quota_exhausted", "daily_limit")

    def diagnostic_fields(self):
        fields = {"can_retry": self.can_retry}
        if isinstance(self.code, str) and self.code in self.DIAGNOSTIC_CODES:
            fields["code"] = self.code
        for key in ("http_status", "provider_code", "limit_kind", "retry_after_seconds"):
            value = getattr(self, key)
            if value is not None:
                fields[key] = value
        return fields


class AiRequestInterrupted(Exception):
    """A run was paused or stopped before another model request could be made."""


def decode(content: str) -> dict:
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        record(
            "ai.response.invalid_json",
            level="ERROR",
            code="invalid_json",
            char_count=len(content),
            json_error_position=exc.pos,
            json_error_line=exc.lineno,
            json_error_column=exc.colno,
        )
        raise AiFailure(
            "invalid_json", "模型已返回，但内容不是有效 JSON，本次结果不可用，请重新评估"
        ) from None
    except (ValueError, TypeError):
        raise AiFailure("invalid_json") from None
    if not isinstance(result, dict):
        raise AiFailure("invalid_json")
    return result


def strings(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
        raise AiFailure("invalid_fields")
    return tuple(s.strip() for s in value)


def text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AiFailure("empty_content")
    return value.strip()


def parse_assessment(payload: dict) -> Assessment:
    try:
        score = payload["score"]
        if type(score) is not int or not 0 <= score <= 100:
            raise AiFailure("invalid_score")
        return Assessment(
            score,
            text(payload["reason"]),
            strings(payload["evidence"]),
            strings(payload["gaps"]),
            strings(payload["missing"]),
        )
    except (KeyError, TypeError):
        raise AiFailure("invalid_fields") from None


def parse_greeting(payload: dict) -> Greeting:
    try:
        return Greeting(text(payload["text"]), strings(payload["evidence"]))
    except (KeyError, TypeError):
        raise AiFailure("invalid_fields") from None
