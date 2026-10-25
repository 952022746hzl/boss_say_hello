import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.test_agent_engine import assessment_args, environment, prepare


async def pending_job(config, snapshot, job, storage, tmp_path):
    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "查询"})
    await ex.execute("assess_job", assessment_args("unknown"))
    return ex


async def test_pending_background_does_not_offer_greeting_tools(config, snapshot, job, storage, tmp_path):
    from boss_say_hello.agent.engine import AgentEngine

    ex = await pending_job(config, snapshot, job, storage, tmp_path)
    names = {t["function"]["name"] for t in AgentEngine(ex, SimpleNamespace()).available_tools()}
    assert "draft_greeting" not in names and "send_greeting" not in names
    assert ex.summary()["jobs"][0]["background_status"] == "pending"
    assert not ex.summary()["jobs"][0]["can_draft"]


async def test_model_request_for_blocked_greeting_is_explained_without_error(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine
    from boss_say_hello.diagnostics import EventLog

    ex = await pending_job(config, snapshot, job, storage, tmp_path)
    # The run was persisted by setup; allow the engine to reuse this isolated fixture.
    storage.db.execute("DELETE FROM runs WHERE id=?", (ex.run.id,))
    storage.db.commit()

    def decision(name, **args):
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": name,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"reason": "合成测试", **args})},
                }
            ],
        }

    ai = SimpleNamespace(
        decide=AsyncMock(side_effect=[decision("draft_greeting", job_id="j1"), decision("finish")])
    )
    entries = []
    log = EventLog(tmp_path / "log", entries.append)
    try:
        with log.bind():
            await AgentEngine(ex, ai).run()
        assert not any(e["level"] == "ERROR" for e in entries)
        refused = next(e for e in entries if e["event"] == "agent.tool.blocked")
        assert "背景" in refused["message"] and "待确认" in refused["message"]
        ex.ai.greet.assert_not_called()
        result = [
            e
            for e in storage.events(ex.run.id)
            if e.kind == "tool_result" and e.payload["tool"] == "draft_greeting"
        ][-1]
        assert result.payload["result"]["status"] == "pending"
    finally:
        log.close()


async def test_only_eligible_job_ids_are_offered_and_invalidation_revokes_them(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine

    ex, *_ = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "身份"})
    await ex.execute("assess_job", assessment_args("clear"))
    ex.jobs["j2"] = job(id="j2")
    engine = AgentEngine(ex, SimpleNamespace())
    offered = {t["function"]["name"]: t["function"] for t in engine.available_tools()}
    assert offered["draft_greeting"]["parameters"]["properties"]["job_id"]["enum"] == ["j1"]
    assert "send_greeting" not in offered
    ex.greetings["j1"] = object()
    assert "send_greeting" in {t["function"]["name"] for t in engine.available_tools()}
    ex.invalidate("j1")
    assert not {"draft_greeting", "send_greeting"} & {t["function"]["name"] for t in engine.available_tools()}
