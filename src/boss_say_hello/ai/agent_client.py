import time

from boss_say_hello.ai.client import AiService, request_failure
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.diagnostics import failure, record


class AgentClient(AiService):
    async def decide(self, messages, tools):
        started = time.monotonic()
        record("ai.request.started", stage="agent_decision", message_count=len(messages))
        try:
            response = await self.client.chat.completions.create(
                model=self.config.model, messages=messages, tools=tools, tool_choice="required"
            )
            choice = response.choices[0]
            if choice.finish_reason in {"length", "content_filter"} or not choice.message.tool_calls:
                raise AiFailure("invalid_decision", "模型未返回完整工具决策，本轮停止")
            # Kimi requires the original reasoning/tool-call message on subsequent turns.
            message = choice.message.model_dump(exclude_none=True)
            record(
                "ai.request.succeeded",
                stage="agent_decision",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            return message
        except Exception as exc:
            error = exc if isinstance(exc, AiFailure) else request_failure(exc)
            failure("ai.request.failed", exc, stage="agent_decision", code=error.code)
            raise error from None
