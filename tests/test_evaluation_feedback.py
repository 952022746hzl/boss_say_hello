import json

import httpx
import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.ai.agent_client import AgentClient
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.diagnostics import EventLog
from tests.test_agent_engine import environment, evaluation_payload


@pytest.mark.parametrize(
    "kind,path",
    [
        ("score_type", "$.assessment.score"),
        ("missing", "$.assessment.missing"),
        ("enum", "$.background.outsourcing"),
        ("extra", "$.assessment"),
    ],
)
async def test_real_client_correction_gets_safe_field_feedback_then_sends_once(
    config, snapshot, job, storage, tmp_path, kind, path
):
    ex, _, search, delivery, _ = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    invalid = evaluation_payload()
    marker = "PRIVATE_MODEL_VALUE_MUST_NOT_ENTER_DIAGNOSTICS"
    if kind == "score_type":
        invalid["assessment"]["score"] = marker
    elif kind == "missing":
        invalid["assessment"].pop("missing")
    elif kind == "enum":
        invalid["background"]["outsourcing"] = marker
    else:
        invalid["assessment"][marker] = marker
    bodies = []
    responses = [
        invalid,
        evaluation_payload(),
        {"text": "您好，我有相关 Python 开发经验，希望进一步沟通。", "evidence": ["Python"]},
    ]

    def respond(request):
        bodies.append(json.loads(request.content))
        payload = responses[len(bodies) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    }
                ]
            },
        )

    log = EventLog(tmp_path / "logs")
    ai = AgentClient(ex.run.config, "synthetic-key", httpx.MockTransport(respond))
    ex.ai = ai
    try:
        with log.bind():
            await AgentEngine(ex, ai).run()
        assert len(bodies) == 3  # failed assessment, one correction, then greeting
        feedback = json.loads(bodies[1]["messages"][-1]["content"])["上次校验反馈"]
        assert feedback["code"] == "invalid_fields"
        assert path in feedback["error"]
        assert marker not in json.dumps(feedback)
        errors = [e for e in log.tail() if e["event"] == "agent.tool.failed"]
        assert len(errors) == 1 and path in errors[0]["message"]
        assert marker not in log.path.read_text(encoding="utf-8")
        assert marker not in "".join(
            p.read_text(encoding="utf-8") for p in (log.root / "ai_requests").glob("*.json")
        )
        assert ex.model_failures[("j1", "assess_job")] == 1
        assert ex.assessments["j1"].score == 76
        search.search.assert_awaited_once()
        delivery.deliver.assert_awaited_once()
    finally:
        await ai.close()
        log.close()


async def test_invalid_response_still_skips_after_one_correction_without_sending(
    config, snapshot, job, storage, tmp_path
):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    invalid = evaluation_payload()
    invalid["background"]["identity_verified"] = "true"
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(invalid)}}]})

    ai = AgentClient(ex.run.config, "synthetic-key", httpx.MockTransport(respond))
    ex.ai = ai
    try:
        await AgentEngine(ex, ai).run()
    finally:
        await ai.close()
    assert count == 2
    assert "j1" in ex.done and not ex.greetings and not ex.assessments
    delivery.deliver.assert_not_called()
    assert any(e.kind == "check" and e.payload["status"] == "pending" for e in events)
    errors = [e.payload["result"].get("error", "") for e in events if e.kind == "tool_result"]
    assert any("$.background.identity_verified" in error for error in errors)


async def test_unexpected_validation_exception_stays_generic(config, snapshot, job, monkeypatch):
    def respond(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(evaluation_payload())}}]}
        )

    def invalid_validator(*args):
        raise ValueError("PRIVATE_UNEXPECTED_EXCEPTION")

    monkeypatch.setattr("boss_say_hello.ai.agent_client.validate_value", invalid_validator)
    ai = AgentClient(config(), "synthetic-key", httpx.MockTransport(respond))
    try:
        with pytest.raises(AiFailure) as caught:
            await ai.evaluate_job(snapshot(), job(), {}, [])
        assert caught.value.code == "invalid_fields"
        assert "PRIVATE_UNEXPECTED_EXCEPTION" not in str(caught.value)
    finally:
        await ai.close()
