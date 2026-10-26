import json
import time
from dataclasses import asdict

import httpx
from openai import APIStatusError, AsyncOpenAI

from boss_say_hello.ai.context import ContextBuilder, budget
from boss_say_hello.ai.prompts import BOUNDARY, RESUME_SCHEMA
from boss_say_hello.ai.recovery import RequestRecovery, request_failure
from boss_say_hello.ai.request_gate import RequestGate
from boss_say_hello.ai.request_trace import RequestTrace
from boss_say_hello.ai.schemas import (
    AiFailure,
    AiRequestInterrupted,
    decode,
    parse_greeting,
    strings,
    text,
)
from boss_say_hello.diagnostics import failure, record
from boss_say_hello.greetings.salutation import address_greeting
from boss_say_hello.greetings.service import GREETING_MAX_LENGTH, message_length
from boss_say_hello.storage.credentials import normalize_secret


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
        self.recovery = RequestRecovery()
        self.request_gate = RequestGate()
        self.client = AsyncOpenAI(
            api_key=secret,
            base_url=config.base_url,
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(connect=15, read=config.model_timeout_seconds, write=30, pool=15),
            ),
        )

    async def completion(self, messages, *, stage, **options):
        trace = RequestTrace(
            stage,
            self.config.base_url.rstrip("/") + "/chat/completions",
            {"model": self.config.model, "messages": messages, **options},
            secret=self.client.api_key,
        )

        async def request():
            return await self.client.chat.completions.create(
                model=self.config.model, messages=messages, **options
            )

        with trace.bind():
            async with self.request_gate.slot(control=self.recovery.control):
                return await self.recovery.call(lambda: trace.call(request), stage=stage)

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
            if stage in {"resume_extract", "assessment", "greeting", "greeting_shorten"}:
                options["response_format"] = {"type": "json_object"}
            response = await self.completion(messages, stage=stage, **options)
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
        except AiRequestInterrupted:
            raise
        except AiFailure as exc:
            if exc.is_limit:
                record(
                    "ai.request.limited",
                    level="WARNING",
                    stage=stage,
                    reason=exc.public_message,
                    **exc.diagnostic_fields(),
                )
            else:
                failure(
                    "ai.request.failed",
                    exc,
                    stage=stage,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    **exc.diagnostic_fields(),
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

    async def greet(self, run, job, assessment):
        messages = ContextBuilder.greeting_messages(run, job, assessment)
        greeting = parse_greeting(decode(await self._request(messages, stage="greeting")))
        greeting = address_greeting(greeting, job.recruiter_name)
        if message_length(greeting.text) > GREETING_MAX_LENGTH:
            record(
                "greeting.shorten.started",
                char_count=message_length(greeting.text),
                limit=GREETING_MAX_LENGTH,
            )
            shortened = parse_greeting(
                decode(
                    await self._request(
                        messages
                        + [
                            {
                                "role": "assistant",
                                "content": json.dumps(asdict(greeting), ensure_ascii=False),
                            },
                            {
                                "role": "user",
                                "content": f"上述草稿过长。请仅压缩表达，保留指定称谓及最相关真实经历，不增加事实。正文控制在80—150字符，含称谓绝不超过{GREETING_MAX_LENGTH}字符；英文每个字母也计入。仍返回相同JSON结构。",
                            },
                        ],
                        stage="greeting_shorten",
                    )
                )
            )
            greeting = address_greeting(shortened, job.recruiter_name)
            if message_length(greeting.text) > GREETING_MAX_LENGTH:
                raise AiFailure(
                    "greeting_too_long",
                    f"招呼语压缩一次后仍为 {message_length(greeting.text)} 个字符，超过 {GREETING_MAX_LENGTH} 上限；未发送，不再自动压缩",
                )
            record(
                "greeting.shorten.succeeded",
                char_count=message_length(greeting.text),
                limit=GREETING_MAX_LENGTH,
            )
        return greeting

    async def close(self):
        await self.client.close()
