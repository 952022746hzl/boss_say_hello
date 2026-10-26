import json

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.diagnostics import EventLog
from tests.test_agent_engine import environment, evaluation_payload, evaluator, prepare


async def ready(config, snapshot, job, storage, tmp_path):
    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "来源"})
    return ex


async def test_invalid_evidence_corrects_once_with_explicit_allowed_ids(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    invalid = evaluation_payload()
    invalid["background"]["source_ids"] = ["guessed-source"]
    ai = evaluator()
    ai.evaluate_job.side_effect = [invalid, evaluation_payload()]
    entries = []
    log = EventLog(tmp_path / "logs", entries.append)
    try:
        with log.bind():
            await AgentEngine(ex, ai).run()
    finally:
        log.close()
    feedback = ai.evaluate_job.call_args_list[1].kwargs["correction"]
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
    invalid = evaluation_payload()
    invalid["background"]["source_ids"] = ["guessed-source"]
    ai = evaluator()
    ai.evaluate_job.return_value = invalid
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done
    assert "j1" not in ex.assessments and "j1" not in ex.greetings
    assert ai.evaluate_job.await_count == 2
    assert any(e.kind == "check" and e.payload.get("status") == "pending" for e in storage.events(ex.run.id))
    ex.delivery.deliver.assert_not_called()
    assert ex.search.search.await_count == 1


async def test_malformed_assessment_also_stops_after_one_correction(config, snapshot, job, storage, tmp_path):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    ai = evaluator()
    ai.evaluate_job.return_value["background"]["score"] = "eighty"
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done and "j1" not in ex.backgrounds
    assert ai.evaluate_job.await_count == 2
    ex.delivery.deliver.assert_not_called()


async def test_invalid_score_after_valid_background_can_be_corrected(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    invalid = evaluation_payload()
    invalid["assessment"]["evidence"] = []
    # Parsing fails after background is otherwise valid; don't leave it blocking the correction.
    invalid["assessment"]["reason"] = ""
    ai = evaluator()
    ai.evaluate_job.side_effect = [invalid, evaluation_payload()]
    await AgentEngine(ex, ai).run()
    assert ai.evaluate_job.await_count == 2
    assert ex.assessments["j1"].score == 76
    assert ex.ai.greet.await_count == 1


async def test_exhausted_job_does_not_consume_next_jobs_correction(config, snapshot, job, storage, tmp_path):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    ex.jobs["j2"] = job(id="j2")
    ex.job_steps["j1"] = 12
    ex.browser.refresh_job.return_value = job(id="j2")
    invalid = evaluation_payload()
    invalid["background"]["source_ids"] = ["wrong"]
    ai = evaluator()
    ai.evaluate_job.side_effect = [invalid, evaluation_payload("suspected")]
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done and "j2" in ex.done
    assert ex.model_failures[("j2", "assess_job")] == 1
    assert ("j1", "assess_job") not in ex.model_failures


async def test_model_action_injection_cannot_run_tool_and_is_bounded(
    config, snapshot, job, storage, tmp_path
):
    ex = await ready(config, snapshot, job, storage, tmp_path)
    ai = evaluator()
    ai.evaluate_job.return_value = {"tool_calls": [{"function": {"name": "send_greeting"}}]}
    await AgentEngine(ex, ai).run()
    assert "j1" in ex.done and "j1" not in ex.assessments
    assert ex.model_failures[("j1", "assess_job")] == 2
    ex.delivery.deliver.assert_not_called()
