import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.domain import Greeting
from tests.test_agent_engine import assessment_args, environment


def evaluator(category="clear"):
    args = assessment_args(category)
    return SimpleNamespace(
        evaluate_job=AsyncMock(return_value={k: args[k] for k in ("assessment", "background")})
    )


async def test_program_advances_one_job_without_mechanical_model_decisions(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, search, delivery, events = environment(
        config, snapshot, job, storage, tmp_path, auto_send=True
    )
    ai = evaluator()
    ex.ai.greet.return_value = Greeting("您好，具有相关 Python 开发经验，希望进一步沟通。", ())
    await AgentEngine(ex, ai).run()
    assert ai.evaluate_job.await_count == 1
    assert ex.ai.greet.await_count == 1
    assert search.search.await_count == 1
    assert delivery.deliver.await_count == 1
    actions = [(e.payload["tool"], e.payload.get("actor")) for e in events if e.kind == "tool_action"]
    assert ("read_job", "program") in actions
    assert ("search_company", "program") in actions
    assert ("send_greeting", "program") in actions
    assert events[-1].payload["status"] == "completed"


async def test_real_client_makes_exactly_two_model_http_requests_for_successful_path(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.ai.agent_client import AgentClient
    from tests.test_agent_engine import evaluation_payload

    ex, _, search, delivery, events = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        payload = (
            evaluation_payload()
            if len(bodies) == 1
            else {"text": "您好，我有 Python 开发经验，希望进一步沟通。", "evidence": ["Python"]}
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
                    }
                ]
            },
        )

    ai = AgentClient(ex.run.config, "synthetic-key", httpx.MockTransport(respond))
    ex.ai = ai
    try:
        await AgentEngine(ex, ai).run()
    finally:
        await ai.close()
    assert len(bodies) == 2
    assert all("tools" not in body and "tool_choice" not in body for body in bodies)
    assert "一次综合评估" in bodies[0]["messages"][0]["content"]
    assert "招呼语" in bodies[1]["messages"][0]["content"]
    search.search.assert_awaited_once()
    delivery.deliver.assert_awaited_once()
    assert events[-1].payload["status"] == "completed"


@pytest.mark.parametrize("case", ["hard", "no_sources", "outsourcing", "low_score"])
async def test_program_skips_ineligible_job_without_asking_model_to_skip(
    config, snapshot, job, storage, tmp_path, case
):
    ex, browser, search, delivery, events = environment(
        config, snapshot, job, storage, tmp_path, auto_send=True
    )
    ai = evaluator("suspected" if case == "outsourcing" else "clear")
    if case == "hard":
        browser.refresh_job.return_value = job(city="上海")
    if case == "no_sources":
        search.search.return_value = []
    if case == "low_score":
        ai.evaluate_job.return_value["assessment"]["score"] = 20
    await AgentEngine(ex, ai).run()
    assert ai.evaluate_job.await_count == (0 if case in {"hard", "no_sources"} else 1)
    ex.ai.greet.assert_not_called()
    delivery.deliver.assert_not_called()
    assert "j1" in ex.done
    assert events[-1].payload["status"] == "completed"
