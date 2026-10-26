import asyncio
from dataclasses import replace

import pytest


async def test_preview_records_greeting_without_contact(setup_agent, storage):
    x = setup_agent()
    await x.engine.run()
    assert not x.browser.calls
    assert any(e.kind == "greeting" for e in storage.events("r1"))
    assert x.events[-1].payload["status"] == "completed"


@pytest.mark.parametrize("change", ["failure", "salary", "contacted", "score"])
async def test_ineligible_never_sends(change, setup_agent, snapshot, agent_config, job):
    j = (
        job(salary_raw="面议")
        if change == "salary"
        else job(contact_status="contacted")
        if change == "contacted"
        else job()
    )
    x = setup_agent(snapshot(config=agent_config(auto_send=True)), [j])
    if change == "failure":
        x.ai.evaluate_job.side_effect = ValueError("failure")
    if change == "score":
        x.ai.evaluate_job.return_value["assessment"]["score"] = 12
    await x.engine.run()
    assert x.browser.calls == []
    x.ai.greet.assert_not_called()
    assert "j1" in x.executor.done


async def test_limit_interval_and_snapshot(setup_agent, snapshot, agent_config, job):
    run = snapshot(config=agent_config(auto_send=True, max_contacts=2, interval_seconds=9))
    x = setup_agent(run, [job(id=f"j{i}") for i in range(5)])
    times = []
    x.browser.after = lambda stage: times.append(x.clock.monotonic()) if stage == "contact" else None
    edited = replace(run.config, max_contacts=99)
    await x.engine.run()
    assert edited.max_contacts == 99 and run.config.max_contacts == 2
    assert len(times) == 2 and times[1] - times[0] >= 9
    assert x.journal.contact_count(run.id) == x.journal.success_count(run.id) == 2
    assert x.events[-1].payload["status"] == "limit_reached"


async def test_pause_resume_keeps_success_goal_and_skips_partial_contact(
    setup_agent, snapshot, agent_config, job
):
    run = snapshot(config=agent_config(auto_send=True, max_contacts=2))
    x = setup_agent(run, [job(id=f"j{i}") for i in range(3)])
    paused = asyncio.Event()

    def emit(event):
        x.events.append(event)
        if event.kind == "state" and event.payload["status"] == "paused":
            paused.set()

    x.executor.emit = emit
    x.browser.after = lambda stage: (
        x.control.pause_requested.set()
        if stage == "contact" and x.browser.calls.count("contact") == 1
        else None
    )
    task = asyncio.create_task(x.engine.run())
    try:
        await asyncio.wait_for(paused.wait(), 1)
        assert x.browser.calls == ["contact"]
        x.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        x.control.closing.set()
        await task
    assert x.browser.calls == ["contact", "contact", "message", "contact", "message"]
    assert x.journal.contact_count(run.id) == 3
    assert x.journal.success_count(run.id) == 2


async def test_account_switch_pauses(setup_agent):
    x = setup_agent()
    x.browser.account = "other"
    paused = asyncio.Event()

    def emit(event):
        x.events.append(event)
        if event.kind == "state" and event.payload["status"] == "paused":
            paused.set()

    x.executor.emit = emit
    task = asyncio.create_task(x.engine.run())
    try:
        await asyncio.wait_for(paused.wait(), 1)
        assert not x.browser.calls
        x.ai.evaluate_job.assert_not_called()
        x.browser.account = "a1"
        x.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        x.control.closing.set()
        await task
    assert any(e.kind == "greeting" for e in x.events)
