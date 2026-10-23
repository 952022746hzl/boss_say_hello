import time

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from boss_say_hello.ai.context import ContextBuilder, budget
from boss_say_hello.ai.prompts import BOUNDARY, RESUME_SCHEMA
from boss_say_hello.ai.schemas import AiFailure, decode, parse_assessment, parse_greeting, strings, text
from boss_say_hello.diagnostics import failure, record
from boss_say_hello.storage.credentials import normalize_secret


def request_failure(exc):
    # Only local, fixed descriptions are public; provider messages can echo private inputs.
    if isinstance(exc, APITimeoutError):
        return AiFailure("request_timeout", "模型响应超时，请稍后重试或检查网络")
    if isinstance(exc, APIConnectionError):
        return AiFailure("connection_failed", "无法连接模型服务，请检查 API 地址、网络、代理或证书")
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        code, message = {
            400: ("bad_request", "模型服务拒绝请求参数，请核对模型名称及接口支持的参数"),
            401: ("authentication_failed", "密钥验证失败，请重新填写该 API 地址对应的密钥"),
            403: ("permission_denied", "模型访问被拒绝，请检查账号权限及模型使用资格"),
            404: ("not_found", "接口或模型不存在，请核对 API 基础地址和模型名称"),
            429: ("rate_limited", "请求受到限制，请检查账户额度、余额或请求频率"),
        }.get(
            status,
            ("service_unavailable", "模型服务暂时异常，请稍后重试")
            if status >= 500
            else ("http_error", "模型服务拒绝请求，请检查接口配置"),
        )
        return AiFailure(code, f"{message}（HTTP {status}）")
    return AiFailure("request_failed", "模型请求或响应处理失败，请查看运行日志中的错误类型")


class AiService:
    def __init__(self, config, secret: str, transport=None):
        config.validate(ready=True)
        if not secret.strip():
            raise AiFailure("missing_key", "请填写 API 密钥")
        try:
            secret = normalize_secret(secret)
        except ValueError as exc:
            raise AiFailure("invalid_key", str(exc)) from None
        self.config = config
        self.client = AsyncOpenAI(
            api_key=secret,
            base_url=config.base_url,
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(connect=15, read=config.model_timeout_seconds, write=30, pool=15),
            ),
        )

    async def _request(self, messages, stage="request"):
        budget(messages)
        started = time.monotonic()
        record(
            "ai.request.started",
            stage=stage,
            char_count=sum(len(m["content"]) for m in messages),
            read_timeout_seconds=self.config.model_timeout_seconds,
        )
        try:
            options = {}
            if stage in {"resume_extract", "assessment", "greeting"}:
                options["response_format"] = {"type": "json_object"}
            response = await self.client.chat.completions.create(
                model=self.config.model, messages=messages, **options
            )
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise AiFailure("response_truncated", "模型输出达到长度限制，结果不完整，本次结果不可用")
            if choice.finish_reason == "content_filter":
                raise AiFailure("response_filtered", "模型服务未提供完整结果，本次结果不可用")
            result = text(choice.message.content)
            record(
                "ai.request.succeeded",
                stage=stage,
                duration_ms=round((time.monotonic() - started) * 1000),
                char_count=len(result),
                finish_reason=choice.finish_reason,
            )
            return result
        except AiFailure as exc:
            failure(
                "ai.request.failed",
                exc,
                stage=stage,
                duration_ms=round((time.monotonic() - started) * 1000),
                code=exc.code,
            )
            raise
        except Exception as exc:
            public_error = request_failure(exc)
            details = {
                "code": public_error.code,
                "read_timeout_seconds": self.config.model_timeout_seconds,
            }
            if isinstance(exc, APIStatusError):
                details["http_status"] = exc.status_code
            failure(
                "ai.request.failed",
                exc,
                stage=stage,
                duration_ms=round((time.monotonic() - started) * 1000),
                **details,
            )
            raise public_error from None

    async def test_connection(self):
        await self._request([{"role": "user", "content": "Reply OK."}], stage="connection_test")

    async def extract_resume(self, source: str):
        payload = decode(
            await self._request(
                [
                    {"role": "system", "content": BOUNDARY + RESUME_SCHEMA},
                    {"role": "user", "content": source},
                ],
                stage="resume_extract",
            )
        )
        try:
            keywords, summary = strings(payload["keywords"]), text(payload["summary"])
            if not keywords:
                raise AiFailure("empty_keywords")
            return keywords, summary
        except (KeyError, TypeError):
            raise AiFailure("invalid_fields") from None

    async def assess(self, run, job):
        return parse_assessment(
            decode(await self._request(ContextBuilder.assessment_messages(run, job), stage="assessment"))
        )

    async def greet(self, run, job, assessment):
        return parse_greeting(
            decode(
                await self._request(ContextBuilder.greeting_messages(run, job, assessment), stage="greeting")
            )
        )

    async def close(self):
        await self.client.close()
