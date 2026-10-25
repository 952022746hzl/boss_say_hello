import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.diagnostics import EventLog
from tests.test_agent_engine import assessment_args, environment, prepare


def decision(name, **args):
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": name,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"reason": "合成检查", **args})},
            }
        ],
    }


async def ready(config, snapshot, job, storage, tmp_path):
    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "来源"})
    return ex


async def test_assessment_schema_only_offers_actual_source_ids(config, snapshot, job, storage, tmp_path):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    tool = next(
        t["function"]
        for t in AgentEngine(ex, None).available_tools()
        if t["function"]["name"] == "assess_job"
    )
    refs = tool["parameters"]["properties"]["background"]["properties"]["source_ids"]
    assert refs["items"]["enum"] == ["e1"]
    assert "enum" not in tool["parameters"]["properties"]["reason"]
    scoring = tool["parameters"]["properties"]["assessment"]["properties"]
    assert "enum" not in scoring["reason"]
    assert all("enum" not in scoring[key]["items"] for key in ("evidence", "gaps", "missing"))
    assert "enum" not in tool["parameters"]["properties"]["background"]["properties"]["reason"]
    assert "source_ids" in ex.summary()["current"]
    assert ex.summary()["current"]["source_ids"] == ["e1"]


async def test_invalid_evidence_corrects_once_with_explicit_allowed_ids(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    invalid = assessment_args()
    invalid["background"]["source_ids"] = ["guessed-source"]
    ai = SimpleNamespace(
        decide=AsyncMock(
            side_effect=[
                decision("assess_job", **invalid),
                decision("assess_job", **assessment_args()),
                decision("finish"),
            ]
        )
    )
    entries = []
    log = EventLog(tmp_path / "logs", entries.append)
    try:
        with log.bind():
            await AgentEngine(ex, ai).run()
    finally:
        log.close()
    feedback = json.loads(ai.decide.call_args_list[1].args[0][-1]["content"])
    assert feedback["code"] == "unknown_evidence"
    assert feedback["allowed_source_ids"] == ["e1"]
    assert feedback["corrections_remaining"] == 1
    assert ex.assessments["j1"].score == 76
    failed = next(e for e in entries if e["event"] == "agent.tool.failed")
    assert failed["fields"]["code"] == "unknown_evidence"
    assert "未收集的证据" in failed["message"]
    assert "guessed-source" not in json.dumps(entries)


async def test_repeated_bad_evidence_becomes_pending_without_more_assessments(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    invalid = assessment_args()
    invalid["background"]["source_ids"] = ["guessed-source"]
    ai = SimpleNamespace(
        decide=AsyncMock(
            side_effect=[
                decision("assess_job", **invalid),
                decision("assess_job", **invalid),
                decision("finish"),
            ]
        )
    )
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done
    assert "j1" not in ex.assessments and "j1" not in ex.greetings
    assert {t["function"]["name"] for t in ai.decide.call_args_list[2].args[1]} == {"finish"}
    events = storage.events(ex.run.id)
    assert any(e.kind == "check" and e.payload.get("status") == "pending" for e in events)
    ex.delivery.deliver.assert_not_called()
    assert ex.search.search.await_count == 1


async def test_malformed_assessment_arguments_also_stop_after_one_correction(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    invalid = assessment_args()
    invalid["background"]["score"] = "eighty"
    ai = SimpleNamespace(
        decide=AsyncMock(
            side_effect=[
                decision("assess_job", **invalid),
                decision("assess_job", **invalid),
                decision("finish"),
            ]
        )
    )
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done
    assert "j1" not in ex.backgrounds
    ex.delivery.deliver.assert_not_called()


async def test_exhausted_job_does_not_consume_next_jobs_correction(config, snapshot, job, storage, tmp_path):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    ex.jobs["j2"] = job(id="j2")
    ex.job_steps["j1"] = ex.run.config.max_job_steps
    ex.browser.refresh_job.return_value = job(id="j2")
    invalid = assessment_args()
    invalid["job_id"] = "j2"
    invalid["background"]["source_ids"] = ["wrong"]
    ai = SimpleNamespace(
        decide=AsyncMock(
            side_effect=[
                decision("assess_job", **assessment_args()),
                decision("read_job", job_id="j2"),
                decision("search_company", job_id="j2"),
                decision("assess_job", **invalid),
                decision("finish"),
            ]
        )
    )
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done and "j2" not in ex.done
    assert ex.model_failures[("j2", "assess_job")] == 1


async def test_multiple_calls_in_one_response_consume_only_one_correction(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    message = decision("assess_job", **assessment_args())
    message["tool_calls"] *= 3
    ai = SimpleNamespace(decide=AsyncMock(side_effect=[message, decision("finish")]))
    await AgentEngine(ex, ai).run()
    assert "j1" not in ex.done and "j1" not in ex.assessments
    assert ex.model_failures[("j1", "assess_job")] == 1
