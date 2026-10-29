from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.domain import Greeting, RunEvent
from tests.test_agent_engine import assessment_args, environment, evaluator, prepare


def offered(ex):
    jid = ex.active_job_id()
    if jid is None:
        return {"finish" if ex.list_ended else "list_jobs"}
    return {AgentEngine(ex, SimpleNamespace()).next_action(jid)[0]}


async def test_active_job_cannot_be_abandoned_to_read_or_search_other_companies(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, search, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    ex.jobs["j2"] = job(id="j2")
    await ex.execute("read_job", {"job_id": "j1", "reason": "当前岗位"})
    for name, args in [
        ("read_job", {"job_id": "j2"}),
        ("search_company", {"job_id": "j2"}),
        ("list_jobs", {}),
    ]:
        with pytest.raises(ValueError, match="当前岗位"):
            await ex.execute(name, {"reason": "不能切换", **args})
    assert browser.refresh_job.await_count == 1
    search.search.assert_not_called()
    assert browser.candidate_batch.await_count == 1


async def test_tools_follow_one_job_through_preview_before_next_job(config, snapshot, job, storage, tmp_path):
    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    ex.jobs["j2"] = job(id="j2")
    assert set(offered(ex)) == {"read_job"}
    await ex.execute("read_job", {"job_id": "j1", "reason": "读详情"})
    assert set(offered(ex)) == {"search_company"}
    await ex.execute("search_company", {"job_id": "j1", "reason": "一次背调"})
    assert set(offered(ex)) == {"assess_job"}
    await ex.execute("assess_job", assessment_args())
    assert set(offered(ex)) == {"draft_greeting"}
    ex.ai.greet = AsyncMock(return_value=Greeting("您好"))
    await ex.execute("draft_greeting", {"job_id": "j1", "reason": "生成草稿"})
    assert ex.active_job_id() == "j2"
    assert offered(ex) == {"read_job"}
    assert ex.active_job_id() == "j2"


async def test_refreshed_list_removes_job_and_company_context_but_preserves_history_and_budget(
    config, snapshot, job, storage, tmp_path
):
    from tests.test_agent_failure_gates import ready_to_send

    ex, browser, _, delivery, _ = await ready_to_send(config, snapshot, job, storage, tmp_path)
    ex.jobs.update({"j2": job(id="j2"), "j3": job(id="j3")})
    ex.companies["j2"] = {"name": "旧公司"}
    ex.sources["j2"] = [{"text": "旧公司证据"}]
    ex.details.add("j2")
    browser.preference_snapshot.return_value = {"document": "refreshed"}
    browser.current_candidate_ids = AsyncMock(return_value={"j3", "j4"})
    delivery.deliver.return_value = [
        RunEvent(ex.run.id, "delivery", {"job_id": "j1", "status": "sent", "returned_to_list": True})
    ]
    await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成发送"})
    assert set(ex.jobs) == {"j3"}
    assert "j1" not in ex.companies and "j2" not in ex.sources and "j2" not in ex.details
    assert ex.discovered_count() == 3
    assert any(e.kind == "delivery" and e.payload["status"] == "sent" for e in storage.events(ex.run.id))
    assert "j1" in ex.done and "j2" not in ex.done
    assert ex.active_job_id() == "j3"


async def test_same_document_list_refresh_is_also_synced(config, snapshot, job, storage, tmp_path):
    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    ex.jobs["j2"] = job(id="j2")
    browser.current_candidate_ids = AsyncMock(return_value={"j2"})
    ex.done.add("j1")
    await ex.sync_refreshed_candidates(ex.receipt, "j1")
    assert set(ex.jobs) == {"j2"}
    assert ex.discovered_count() == 2


async def test_auto_mode_sends_current_job_then_reads_fresh_candidates(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, _, delivery, _ = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="old")], "ended": True}
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "综合背调"})
    await ex.execute("assess_job", assessment_args())
    ex.ai.greet = AsyncMock(return_value=Greeting("您好"))
    await ex.execute("draft_greeting", {"job_id": "j1", "reason": "生成"})
    assert ex.active_job_id() == "j1"
    assert set(offered(ex)) == {"send_greeting"}
    browser.current_candidate_ids = AsyncMock(return_value={"new"})
    delivery.deliver.return_value = [
        RunEvent(ex.run.id, "delivery", {"job_id": "j1", "status": "sent", "returned_to_list": True})
    ]
    await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成发送"})
    assert ex.jobs == {} and ex.companies == {} and ex.sources == {}
    assert set(offered(ex)) == {"list_jobs"}
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="old"), job(id="new")], "ended": True}
    await ex.execute("list_jobs", {"reason": "刷新后的候选"})
    assert set(ex.jobs) == {"old", "new"}  # Only completed jobs stay excluded after a refresh.
    assert ex.discovered_count() == 3
    assert ex.active_job_id() == "old"
    delivery.deliver.assert_awaited_once()


async def test_rejected_job_does_not_offer_background_calls(config, snapshot, job, storage, tmp_path):
    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    browser.refresh_job.return_value = job(salary_raw="5-8K")
    await ex.execute("read_job", {"job_id": "j1", "reason": "读取实际薪资"})
    assert set(offered(ex)) == {"skip_job"}


@pytest.mark.parametrize("failed_return", [True, False])
async def test_resume_after_sent_but_failed_refresh_recovers_new_list(
    config, snapshot, job, storage, tmp_path, failed_return
):
    from boss_say_hello.browser.port import BrowserFailure
    from tests.test_agent_failure_gates import ready_to_send

    ex, browser, _, delivery, _ = await ready_to_send(config, snapshot, job, storage, tmp_path)
    ex.jobs["old"] = job(id="old")
    browser.current_candidate_ids = AsyncMock(side_effect=BrowserFailure("列表未加载"))
    delivery.deliver.return_value = [
        RunEvent(
            ex.run.id, "delivery", {"job_id": "j1", "status": "sent", "returned_to_list": not failed_return}
        )
    ]
    if failed_return:
        await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成发送"})
    else:
        with pytest.raises(BrowserFailure):
            await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成发送"})
    assert ex.control.pause_requested.is_set() and ex.receipt is None
    ex.control.pause_requested.clear()
    browser.current_candidate_ids.side_effect = None
    browser.current_candidate_ids.return_value = {"new"}
    browser.candidate_batch.return_value = {"jobs": [job(id="new")], "ended": True}
    await AgentEngine(ex, SimpleNamespace()).prepare_candidates()
    assert set(ex.jobs) == {"new"}
    assert ex.active_job_id() == "new"
    assert "j1" in ex.done and "old" not in ex.done
    assert "j1" not in ex.sources
    delivery.deliver.assert_awaited_once()


async def test_contacted_jobs_are_counted_without_candidate_limit(config, snapshot, job, storage, tmp_path):
    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    browser.candidate_batch.return_value = {"jobs": [job(id=f"j{i}") for i in range(4)], "ended": True}
    ex.journal.eligible = lambda account, jid: False
    await prepare(ex)
    assert ex.discovered_count() == 4
    assert ex.list_ended
    assert set(offered(ex)) == {"finish"}


async def test_unloaded_list_during_resume_remains_resumable(config, snapshot, job, storage, tmp_path):
    from boss_say_hello.browser.port import BrowserFailure

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    ex.done.add("j1")
    ex.candidates_need_sync = True
    ex.receipt = None
    browser.current_candidate_ids = AsyncMock(side_effect=[BrowserFailure("列表仍未加载"), {"new"}])
    browser.candidate_batch.return_value = {"jobs": [job(id="new")], "ended": True}
    resumed = []

    def resume_after_pause(event):
        if event.kind == "state" and event.payload.get("status") == "paused":
            resumed.append(event)
            ex.control.pause_requested.clear()  # Simulate user clicking Continue after restoring the page.

    ex.emit = resume_after_pause
    browser.refresh_job.return_value = job(id="new")
    ai = evaluator("suspected")
    await AgentEngine(ex, ai).run()
    assert resumed
    assert storage.events(ex.run.id)[-1].payload["status"] == "completed"
    assert "new" in ex.done and not ex.candidates_need_sync


async def test_engine_finishes_current_job_before_evaluating_second(config, snapshot, job, storage, tmp_path):
    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="j2")], "ended": True}
    browser.refresh_job.side_effect = lambda j: j
    ai = evaluator()
    seen = []

    async def assess(run, current, company, sources, **kwargs):
        seen.append((current.id, set(ex.done)))
        return {k: assessment_args()[k] for k in ("assessment", "background")}

    ai.evaluate_job.side_effect = assess
    await AgentEngine(ex, ai).run()
    assert {"j1", "j2"} <= ex.done
    assert seen == [("j1", set()), ("j2", {"j1"})]
    assert ex.ai.greet.await_count == 2
    assert ai.evaluate_job.call_args_list[-1].args[1].id == "j2"
