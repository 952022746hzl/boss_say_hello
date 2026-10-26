import time

from boss_say_hello.ai.client import AiService, request_failure
from boss_say_hello.ai.schemas import AiFailure, AiRequestInterrupted
from boss_say_hello.diagnostics import failure, record


class AgentClient(AiService):
    async def decide(self, messages, tools):
        started = time.monotonic()
        record("ai.request.started", stage="agent_decision", message_count=len(messages))
        record(
            "ai.request.details",
            level="DEBUG",
            model=self.config.model,
            tool_count=len(tools),
            message_count=len(messages),
            char_count=sum(len(str(m.get("content", ""))) for m in messages),
        )
        try:
            response = await self.completion(
                messages, stage="agent_decision", tools=tools, tool_choice="required"
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
            usage = getattr(response, "usage", None)
            record(
                "ai.decision.details",
                level="DEBUG",
                finish_reason=choice.finish_reason,
                tool_names=[c.function.name for c in choice.message.tool_calls],
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
            )
            return message
        except AiRequestInterrupted:
            raise
        except Exception as exc:
            error = exc if isinstance(exc, AiFailure) else request_failure(exc)
            if error.is_limit:
                record(
                    "ai.request.limited",
                    level="WARNING",
                    stage="agent_decision",
                    reason=error.public_message,
                    **error.diagnostic_fields(),
                )
            else:
                failure("ai.request.failed", exc, stage="agent_decision", **error.diagnostic_fields())
            raise error from None
