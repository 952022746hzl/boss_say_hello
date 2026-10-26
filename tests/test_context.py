import json
from dataclasses import replace

import httpx
import pytest

from boss_say_hello.ai.agent_client import AgentClient
from boss_say_hello.ai.context import ContextBuilder
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.domain import Assessment, PromptSet
from tests.fakes.agent import evaluation_payload


async def test_three_prompts_have_separate_roles(config, snapshot, job):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(evaluation_payload())}, "finish_reason": "stop"}]},
        )

    run = snapshot()
    run = replace(run, config=replace(run.config, prompts=PromptSet("PREFER", "SCORE", "GREET")))
    ai = AgentClient(config(), "synthetic-key", httpx.MockTransport(respond))
    try:
        await ai.evaluate_job(run, job(), {"name": "Example"}, [])
    finally:
        await ai.close()
    scoring = str(bodies[0]["messages"])
    greeting = str(ContextBuilder.greeting_messages(run, job(), Assessment(80, "reason", (), (), ())))
    assert "PREFER" in scoring and "SCORE" in scoring and "GREET" not in scoring
    assert "GREET" in greeting and "SCORE" not in greeting
    assert "salary_min" in scoring


async def test_context_too_large_fails_without_truncation_or_request(config, snapshot, job):
    requests = []

    def respond(request):
        requests.append(request)
        raise AssertionError("Oversized context must be rejected before a network request")

    ai = AgentClient(config(), "synthetic-key", httpx.MockTransport(respond))
    try:
        with pytest.raises(AiFailure) as caught:
            await ai.evaluate_job(snapshot(), job(text="x" * 50000), {"name": "Example"}, [])
        assert caught.value.code == "context_too_large"
        assert requests == []
    finally:
        await ai.close()
