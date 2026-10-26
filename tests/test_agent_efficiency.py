from types import SimpleNamespace

from tests.test_agent_engine import environment, evaluator


async def test_target_setup_and_first_batch_do_not_require_model_calls(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    ai = evaluator("suspected")
    await AgentEngine(ex, ai).run()
    assert ex.receipt and "j1" in ex.jobs
    assert ai.evaluate_job.await_count == 1
    assert browser.candidate_batch.await_count == 1
    actions = [e for e in storage.events(ex.run.id) if e.kind == "tool_action"]
    assert all(e.payload["actor"] == "program" for e in actions[:3])


async def test_missing_tag_stops_before_model_or_contact(config, snapshot, job, storage, tmp_path):
    from boss_say_hello.agent.engine import AgentEngine

    ex, browser, _, delivery, _ = environment(config, snapshot, job, storage, tmp_path)
    browser.preference_snapshot.return_value = {"expectations": [], "controls": []}
    ai = evaluator()
    await AgentEngine(ex, ai).run()
    ai.evaluate_job.assert_not_called()
    delivery.deliver.assert_not_called()


async def test_pause_during_tag_setup_can_resume(config, snapshot, job, storage, tmp_path):
    import asyncio

    from boss_say_hello.agent.engine import AgentEngine

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    original = browser.preference_snapshot.return_value

    async def observed():
        ex.control.pause_requested.set()
        browser.preference_snapshot.side_effect = None
        return original

    browser.preference_snapshot.side_effect = observed
    ai = evaluator("suspected")
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        await asyncio.sleep(0.03)
        assert not task.done()
        ai.evaluate_job.assert_not_called()
        ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 2)
        assert storage.events(ex.run.id)[-1].payload["status"] == "completed"
    finally:
        ex.control.closing.set()
        await task


async def test_recover_receipt_keeps_completed_jobs_and_does_not_refetch(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    ex.jobs["j1"] = job()
    ex.done.add("j1")
    ex.receipt = None
    await AgentEngine(ex, SimpleNamespace()).prepare_candidates()
    assert ex.receipt is not None and "j1" in ex.done
    browser.candidate_batch.assert_not_called()
