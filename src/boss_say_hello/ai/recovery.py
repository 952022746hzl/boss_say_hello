"""Classify request limits without exposing provider content and wait cooperatively."""

import math
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from boss_say_hello.ai.schemas import AiFailure, AiRequestInterrupted
from boss_say_hello.diagnostics import record
from boss_say_hello.runs.control import Clock


def _retry_after(response):
    value = response.headers.get("retry-after", "").strip()
    if not value or len(value) > 128:
        return None
    try:
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            seconds = float(value)
        else:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = max(0.0, deadline.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _limit_details(exc):
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        try:
            body = exc.response.json()
        except (ValueError, UnicodeError):
            body = {}
    details = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(details, dict):
        details = {}
    codes = [details.get("code"), details.get("type")]
    known = [code for code in codes if isinstance(code, str) and code in AiFailure.PROVIDER_LIMIT_KINDS]
    # Inspect text only in memory. No provider text or unknown field enters AiFailure.
    message = details.get("message", "")
    message = message.lower() if isinstance(message, str) else ""
    kinds = {AiFailure.PROVIDER_LIMIT_KINDS[code] for code in known}
    if "daily" in kinds or re.search(r"\b(?:tpd|rpd|daily)\b|per[\s_-]+day|每日|每天|日额度", message):
        kind = "daily"
    elif "quota" in kinds or re.search(
        r"insufficient[\s_-]+(?:quota|balance|funds|credit)|"
        r"(?:balance|credit)[\s_-]+(?:is[\s_-]+)?(?:insufficient|exhausted|too[\s_-]+low)|"
        r"exceed(?:ed|s)?[\s_-]+(?:your[\s_-]+)?(?:current[\s_-]+)?quota|"
        r"quota[\s_-]+(?:exceeded|exhausted)|余额不足|额度不足|欠费|余额已用尽",
        message,
    ):
        kind = "quota"
    elif "temporary" in kinds or re.search(
        r"\b(?:rpm|tpm|concurrency|concurrent)\b|per[\s_-]+(?:minute|second)|"
        r"overload|请求频率|每分钟|每秒|并发",
        message,
    ):
        kind = "temporary"
    else:
        kind = "unknown"
    return (known[0] if known else None), kind, _retry_after(exc.response)


def request_failure(exc):
    # Preserve existing fixed public descriptions for non-limit errors.
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
        return AiFailure("request_timeout", "模型响应超时，请稍后重试或检查网络")
    if isinstance(exc, (APIConnectionError, httpx.RequestError)):
        return AiFailure("connection_failed", "无法连接模型服务，请检查 API 地址、网络、代理或证书")
    if isinstance(exc, (APIStatusError, httpx.HTTPStatusError)):
        status = exc.response.status_code
        if status == 429:
            provider_code, kind, seconds = _limit_details(exc)
            code, message = {
                "quota": ("quota_exhausted", "账户额度或余额不足，请检查账户并补充额度；不会自动反复重试"),
                "daily": (
                    "daily_limit",
                    "已达到每日请求或令牌额度，请等待额度重置或调整账户限额；不会自动反复重试",
                ),
                "temporary": ("rate_limited", "模型服务暂时限制请求频率，请稍后继续"),
                "unknown": ("rate_limited", "请求受到限制，请检查账户额度、余额或请求频率"),
            }[kind]
            return AiFailure(
                code,
                f"{message}（HTTP {status}）",
                http_status=status,
                provider_code=provider_code,
                limit_kind=kind,
                retry_after_seconds=seconds,
                can_retry=kind == "temporary" or (kind == "unknown" and seconds is not None),
            )
        code, message = {
            400: ("bad_request", "模型服务拒绝请求参数，请核对模型名称及接口支持的参数"),
            401: ("authentication_failed", "密钥验证失败，请重新填写该 API 地址对应的密钥"),
            403: ("permission_denied", "模型访问被拒绝，请检查账号权限及模型使用资格"),
            404: ("not_found", "接口或模型不存在，请核对 API 基础地址和模型名称"),
        }.get(
            status,
            ("service_unavailable", "模型服务暂时异常，请稍后重试")
            if status >= 500
            else ("http_error", "模型服务拒绝请求，请检查接口配置"),
        )
        return AiFailure(code, f"{message}（HTTP {status}）", http_status=status)
    return AiFailure("request_failed", "模型请求或响应处理失败，请查看运行日志中的错误类型")


class RequestRecovery:
    def __init__(self, control=None, clock=None, on_wait=None, on_retry=None):
        self.bind(control, clock, on_wait, on_retry)

    def bind(self, control=None, clock=None, on_wait=None, on_retry=None):
        self.control = control
        self.clock = clock if clock is not None else Clock()
        self.on_wait = on_wait
        self.on_retry = on_retry

    def _check_control(self):
        if self.control and (self.control.pause_requested.is_set() or self.control.closing.is_set()):
            raise AiRequestInterrupted("本轮已暂停或结束，取消等待模型重试")

    async def _wait(self, seconds):
        deadline = self.clock.monotonic() + seconds
        while True:
            self._check_control()
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return
            await self.clock.sleep(min(0.1, remaining))

    async def call(self, operation, *, stage):
        for attempt in range(3):
            self._check_control()
            if attempt > 0:
                record("ai.retry.started", stage=stage, retry=attempt)
                if self.on_retry:
                    self.on_retry(stage, attempt)
            try:
                return await operation()
            except (APIStatusError, httpx.HTTPStatusError) as raw:
                if raw.response.status_code != 429:
                    raise
                error = request_failure(raw)
                if not error.can_retry:
                    raise error from raw
                if attempt == 2:
                    raise AiFailure(
                        public_message="模型服务连续限流，自动重试 2 次仍未恢复，请稍后继续（HTTP 429）",
                        **error.diagnostic_fields(),
                    ) from raw
                seconds = error.retry_after_seconds
                if seconds is None:
                    seconds = 10 * (attempt + 1)
                if seconds > 60:
                    raise AiFailure(
                        public_message=f"模型服务要求等待 {seconds:g} 秒，超过自动等待上限，请稍后继续（HTTP 429）",
                        **error.diagnostic_fields(),
                    ) from raw
                self._check_control()
                record(
                    "ai.retry.wait",
                    level="WARNING",
                    stage=stage,
                    wait_seconds=seconds,
                    retry=attempt + 1,
                    **error.diagnostic_fields(),
                )
                if self.on_wait:
                    self.on_wait(stage, seconds, attempt + 1)
                await self._wait(seconds)
