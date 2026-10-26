import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.browser.port import ActionResult
from boss_say_hello.domain import AppConfig, Greeting, RunEvent
from tests.test_agent_engine import assessment_args, environment, prepare


def decision(name, **args):
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "a",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"reason": "合成测试", **args})},
            }
        ],
    }


async def test_early_finish_is_hidden_and_rejected(config, snapshot, job, storage, tmp_path):
    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    assert "finish" not in {t["function"]["name"] for t in AgentEngine(ex, None).available_tools()}
    with pytest.raises(ValueError, match="尚未"):
        await ex.execute("finish", {"reason": "错误地认为工具不可用"})
    assert not ex.finished
    ex.done.add("j1")
    ex.list_ended = False
    with pytest.raises(ValueError, match="更多"):
        await ex.finish()
    ex.list_ended = True
    assert (await ex.finish())["status"] == "finished"


async def test_repeated_early_finish_uses_only_twelve_decisions_per_job(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, _, delivery, _ = environment(config, snapshot, job, storage, tmp_path)
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="j2")], "ended": True}
    ai = SimpleNamespace(decide=AsyncMock(return_value=decision("finish")))
    await AgentEngine(ex, ai).run()
    assert ai.decide.await_count == 24
    assert ex.done == {"j1", "j2"}
    assert storage.events(ex.run.id)[-1].payload["status"] == "completed"
    delivery.deliver.assert_not_called()


async def test_150_successes_continue_past_failures_and_old_limits(config, snapshot, job, storage, tmp_path):
    ex, browser, search, delivery, _ = environment(
        config, snapshot, job, storage, tmp_path, auto_send=True, max_contacts=150
    )
    from tests.test_engine import FakeClock

    ex.clock = FakeClock()
    jobs = [job(id=f"j{i}") for i in range(155)]
    browser.candidate_batch.return_value = {"jobs": jobs, "ended": True}
    browser.refresh_job.side_effect = lambda j: j
    browser.current_candidate_ids = AsyncMock(return_value={j.id for j in jobs})
    ex.ai.greet = AsyncMock(return_value=Greeting("您好，我有相关项目经验"))

    async def delivered(run, j, *args):
        attempt = ex.journal.reserve(run, j)
        assert attempt
        for stage in ("contact", "message"):
            ex.journal.begin_stage(attempt, stage)
            ex.journal.finish_stage(
                attempt,
                stage,
                ActionResult(
                    "failed" if j.id in {"j0", "j1"} and stage == "message" else "succeeded", "合成回执"
                ),
            )
        return [
            RunEvent(
                run.id,
                "delivery",
                {
                    "job_id": j.id,
                    "status": "failed" if j.id in {"j0", "j1"} else "sent",
                    "returned_to_list": True,
                },
            )
        ]

    delivery.deliver.side_effect = delivered

    async def choose(messages, tools):
        names = {t["function"]["name"] for t in tools}
        for name in ("read_job", "search_company", "assess_job", "draft_greeting", "send_greeting"):
            if name in names:
                args = {"job_id": ex.active_job_id()}
                if name == "assess_job":
                    args = {**assessment_args(), **args}
                return decision(name, **args)
        return decision("finish")

    ai = SimpleNamespace(decide=AsyncMock(side_effect=choose))
    await AgentEngine(ex, ai).run()
    assert delivery.deliver.await_count == 152
    assert ex.journal.contact_count(ex.run.id) == 152
    assert ai.decide.await_count == 760
    assert search.search.await_count == 1  # Same company sources reused, every job assessed again.
    assert len(ex.assessments) == 0  # Completed contexts are cleaned on list return.
    assert sum(e.kind == "assessment" for e in storage.events(ex.run.id)) == 152
    final = next(e.payload for e in storage.events(ex.run.id) if e.kind == "finished")
    assert final["status"] == "limit_reached" and final["contacts"] == 152
    assert final["successes"] == 150
    assert final["discovered"] == 155 and final["contact_limit"] == 150


def test_removed_limits_are_not_editable(qtbot):
    from boss_say_hello.ui.agent_settings import AgentSettings

    page = AgentSettings()
    qtbot.addWidget(page)
    assert set(page.limits) == {"background_weight"}
    assert (
        not {"max_candidates", "max_agent_steps", "max_job_steps", "company_cache_days"}
        & asdict(AppConfig()).keys()
    )


def test_only_verified_messages_count_toward_success_goal(snapshot, config, job, storage):
    from boss_say_hello.delivery.journal import DeliveryJournal

    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True, max_contacts=2))
    for jid, outcome in [
        ("failed", "failed"),
        ("unknown", "unknown"),
        ("ok1", "succeeded"),
        ("ok2", "succeeded"),
    ]:
        attempt = journal.reserve(run, job(id=jid, company="同一公司"))
        assert attempt is not None
        journal.begin_stage(attempt, "contact")
        journal.finish_stage(attempt, "contact", ActionResult("succeeded", "已打开沟通"))
        before = journal.success_count(run.id)
        journal.begin_stage(attempt, "message")
        assert journal.success_count(run.id) == before
        journal.finish_stage(attempt, "message", ActionResult(outcome, "合成回执"))
        assert journal.success_count(run.id) == before + (outcome == "succeeded")
    assert journal.success_count(run.id) == 2
    assert journal.contact_count(run.id) == 4
    assert journal.reserve(run, job(id="extra")) is None
    assert not journal.eligible(run.account_id, "unknown")
    journal.recover_incomplete()
    assert journal.success_count(run.id) == 2


async def test_empty_page_is_not_reported_as_exhausted_before_confirmation(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, _, delivery, _ = environment(config, snapshot, job, storage, tmp_path)
    browser.candidate_batch.side_effect = [
        {"jobs": [], "ended": False},
        {"jobs": [], "ended": False},
        {"jobs": [], "ended": False},
        {"jobs": [], "ended": False},
        {"jobs": [job()], "ended": True},
    ]
    paused = []

    def resume(event):
        if event.kind == "state" and event.payload.get("status") == "paused":
            paused.append(event)
            ex.control.pause_requested.clear()

    ex.emit = resume
    ai = SimpleNamespace(decide=AsyncMock(return_value=decision("skip_job", job_id="j1")))
    await AgentEngine(ex, ai).run()
    assert len(paused) == 1
    assert "未发现新岗位" in paused[0].payload["reason"]
    assert ai.decide.await_count == 1
    assert "j1" in ex.done
    assert storage.events(ex.run.id)[-1].payload["status"] == "completed"
    delivery.deliver.assert_not_called()


def test_progress_and_finished_show_successes_separately_from_attempts(qtbot, tmp_path):
    from unittest.mock import Mock

    from boss_say_hello.diagnostics import EventLog
    from boss_say_hello.ui.run_page import RunPage

    page = RunPage(
        SimpleNamespace(request_pause=Mock(), request_resume=Mock(), request_stop=Mock()), lambda: None
    )
    qtbot.addWidget(page)
    page.on_event(RunEvent("r", "started", {"mode": "auto"}))
    payload = {"successes": 150, "contact_limit": 150, "contacts": 152, "discovered": 155, "handled": 152}
    page.on_event(RunEvent("r", "progress", payload))
    assert "150 / 150" in page.progress.text() and "尝试 152" in page.progress.text()
    final = RunEvent(
        "r", "finished", {**payload, "status": "limit_reached", "reason": "已达到本轮成功发送目标"}
    )
    page.on_event(final)
    assert "成功发送目标" in page.status.text()
    entries = []
    log = EventLog(tmp_path / "logs", entries.append)
    try:
        log.event(final)
        assert "成功 150/150" in entries[-1]["message"]
        assert entries[-1]["fields"]["contacts"] == 152
    finally:
        log.close()


@pytest.mark.parametrize("outcome,successes", [("succeeded", 1), ("unknown", 0)])
async def test_paused_delivery_refreshes_progress_before_waiting(
    config, snapshot, job, storage, tmp_path, outcome, successes
):
    from tests.test_agent_failure_gates import ready_to_send

    ex, browser, _, delivery, _ = await ready_to_send(config, snapshot, job, storage, tmp_path)

    async def delivered(run, j, *args):
        attempt = ex.journal.reserve(run, j)
        for stage in ("contact", "message"):
            ex.journal.begin_stage(attempt, stage)
            ex.journal.finish_stage(
                attempt, stage, ActionResult("succeeded" if stage == "contact" else outcome, "合成回执")
            )
        return [
            RunEvent(
                run.id,
                "delivery",
                {
                    "job_id": j.id,
                    "status": "sent" if outcome == "succeeded" else "unknown",
                    "returned_to_list": False,
                },
            )
        ]

    delivery.deliver.side_effect = delivered
    at_pause = []

    def stop_on_pause(event):
        if event.kind == "state" and event.payload.get("status") == "paused":
            at_pause.append([e.payload for e in storage.events(ex.run.id) if e.kind == "progress"][-1])
            ex.control.closing.set()

    ex.emit = stop_on_pause
    ai = SimpleNamespace(decide=AsyncMock(return_value=decision("send_greeting", job_id="j1")))
    await AgentEngine(ex, ai).run()
    assert at_pause and at_pause[0]["successes"] == successes
    assert at_pause[0]["contacts"] == 1
    assert not ex.journal.eligible(ex.run.account_id, "j1")
    assert ai.decide.await_count == 1
