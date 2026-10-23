import time

import httpx
from openai import AsyncOpenAI

from boss_say_hello.ai.context import ContextBuilder, budget
from boss_say_hello.ai.prompts import BOUNDARY, RESUME_SCHEMA
from boss_say_hello.ai.schemas import AiFailure, decode, parse_assessment, parse_greeting, strings, text
from boss_say_hello.diagnostics import failure, record


class AiService:
    def __init__(self, config, secret: str, transport=None):
        config.validate(ready=True)
        if not secret.strip():
            raise AiFailure("missing_key", "请填写 API 密钥")
        self.config = config
        self.client = AsyncOpenAI(
            api_key=secret,
            base_url=config.base_url,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, timeout=45),
        )

    async def _request(self, messages, stage="request"):
        budget(messages)
        started = time.monotonic()
        record("ai.request.started", stage=stage, char_count=sum(len(m["content"]) for m in messages))
        try:
            response = await self.client.chat.completions.create(
                model=self.config.model, messages=messages, temperature=0.2
            )
            result = text(response.choices[0].message.content)
            record(
                "ai.request.succeeded",
                stage=stage,
                duration_ms=round((time.monotonic() - started) * 1000),
                char_count=len(result),
            )
            return result
        except AiFailure as exc:
            failure(
                "ai.request.failed", exc, stage=stage, duration_ms=round((time.monotonic() - started) * 1000)
            )
            raise
        except Exception as exc:
            failure(
                "ai.request.failed", exc, stage=stage, duration_ms=round((time.monotonic() - started) * 1000)
            )
            raise AiFailure(
                "request_failed", "模型请求失败，请检查地址、密钥、模型名或网络；未进行发送"
            ) from None

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
