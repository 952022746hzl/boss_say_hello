from types import SimpleNamespace

from tests.test_agent_engine import assessment_args, environment, evaluator, prepare


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
    assert AgentEngine(ex, SimpleNamespace()).next_action("j1")[0] == "skip_job"
    assert ex.backgrounds["j1"].status == "pending"
    assert ex.greeting_block("j1")["status"] == "pending"


async def test_program_skips_pending_background_without_model_or_error(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine
    from boss_say_hello.diagnostics import EventLog

    ex = await pending_job(config, snapshot, job, storage, tmp_path)
    ai = evaluator()
    entries = []
    log = EventLog(tmp_path / "log", entries.append)
    try:
        with log.bind():
            await AgentEngine(ex, ai).run()
        assert not any(e["level"] == "ERROR" for e in entries)
        ex.ai.greet.assert_not_called()
        ai.evaluate_job.assert_not_called()
        result = [
            e
            for e in storage.events(ex.run.id)
            if e.kind == "tool_result" and e.payload["tool"] == "skip_job"
        ][-1]
        assert "待确认" in result.payload["result"]["reason"]
    finally:
        log.close()


async def test_next_program_action_respects_eligibility_and_invalidation(
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
    assert ex.active_job_id() == "j1" and engine.next_action("j1")[0] == "draft_greeting"
    ex.greetings["j1"] = object()
    assert engine.next_action("j1")[0] == "send_greeting"
    ex.invalidate("j1")
    assert engine.next_action("j1")[0] == "assess_job"
