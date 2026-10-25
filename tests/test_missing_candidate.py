from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
from boss_say_hello.browser.port import BrowserFailure
from tests.test_agent_engine import environment, prepare


async def test_missing_card_is_distinct_from_broken_or_empty_list():
    from boss_say_hello.browser.port import JobUnavailable

    browser = NodriverAdapter()
    browser.evaluate = AsyncMock(
        side_effect=[False, {"cards": [{"url": "https://www.zhipin.com/job_detail/other.html"}]}]
    )
    with pytest.raises(JobUnavailable):
        await browser.read_detail("https://www.zhipin.com/job_detail/missing.html")
    browser.evaluate = AsyncMock(side_effect=[False, {"cards": []}])
    with pytest.raises(BrowserFailure) as caught:
        await browser.read_detail("https://www.zhipin.com/job_detail/missing.html")
    assert not isinstance(caught.value, JobUnavailable)
    browser.evaluate = AsyncMock(
        side_effect=[False, {"cards": [{"url": "https://www.zhipin.com/job_detail/missing.html"}]}]
    )
    with pytest.raises(BrowserFailure) as caught:
        await browser.read_detail("https://www.zhipin.com/job_detail/missing.html")
    assert not isinstance(caught.value, JobUnavailable)


async def test_missing_candidate_skips_only_that_job_and_clears_send_eligibility(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.browser.port import JobUnavailable
    from boss_say_hello.diagnostics import EventLog

    ex, browser, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    ex.jobs["j2"] = job(id="j2")
    ex.assessments["j1"] = object()
    ex.greetings["j1"] = object()
    browser.refresh_job.side_effect = JobUnavailable("岗位已不在当前列表")
    entries = []
    log = EventLog(tmp_path / "log", entries.append)
    try:
        with log.bind():
            result = await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
        assert result["status"] == "skipped" and result["code"] == "job_unavailable"
        assert ex.done == {"j1"}
        assert not ex.control.pause_requested.is_set()
        assert "j1" not in ex.greetings and "j1" not in ex.assessments
        assert any(e.kind == "check" and e.payload["status"] == "skipped" for e in events)
        assert any(e["level"] == "WARNING" and "岗位" in e["message"] for e in entries)
        assert not any(e["level"] == "ERROR" for e in entries)
        delivery.deliver.assert_not_called()
        browser.refresh_job.side_effect = None
        browser.refresh_job.return_value = job(id="j2")
        assert "job" in await ex.execute("read_job", {"job_id": "j2", "reason": "继续下个岗位"})
    finally:
        log.close()


@pytest.mark.parametrize("changed", ["tag", "account"])
async def test_missing_candidate_does_not_hide_changed_tag_or_account(
    config, snapshot, job, storage, tmp_path, changed
):
    from boss_say_hello.browser.port import JobUnavailable

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)

    async def disappear(_job):
        if changed == "tag":
            browser.verify_preferences.return_value = False
        else:
            browser.session.return_value = SimpleNamespace(account_id="other", logged_in=True)
        raise JobUnavailable("岗位已不在当前列表")

    browser.refresh_job.side_effect = disappear
    with pytest.raises(BrowserFailure):
        await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    assert ex.control.pause_requested.is_set()
    assert "j1" not in ex.done


async def test_returned_refreshed_list_drops_only_missing_pending_candidates(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.domain import RunEvent
    from tests.test_agent_failure_gates import ready_to_send

    ex, browser, _, delivery, events = await ready_to_send(config, snapshot, job, storage, tmp_path)
    ex.jobs.update({"j2": job(id="j2"), "j3": job(id="j3")})
    ex.greetings["j2"] = object()
    browser.preference_snapshot.return_value = {"document": "refreshed"}
    browser.current_candidate_ids = AsyncMock(return_value={"j3", "j4"})
    delivery.deliver.return_value = [
        RunEvent(ex.run.id, "delivery", {"job_id": "j1", "status": "sent", "returned_to_list": True})
    ]
    await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成测试，不真实发送"})
    assert ex.done == {"j1", "j2"}
    assert "j2" not in ex.greetings
    assert not ex.control.pause_requested.is_set()
    assert ex.receipt["document"] == "refreshed"
    assert any(e.kind == "check" and e.payload.get("job_id") == "j2" for e in events)


def test_missing_candidate_result_is_not_presented_as_detail_loaded():
    from boss_say_hello.ui.presentation import tool_result_summary

    result = {"status": "skipped", "code": "job_unavailable", "reason": "岗位已消失，跳过"}
    assert tool_result_summary("read_job", result) == "岗位已消失，跳过"


async def test_unloaded_refreshed_list_pauses_without_losing_pending_jobs_or_retrying_sent_job(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.domain import RunEvent
    from tests.test_agent_failure_gates import ready_to_send

    ex, browser, _, delivery, _ = await ready_to_send(config, snapshot, job, storage, tmp_path)
    ex.jobs["j2"] = job(id="j2")
    browser.preference_snapshot.return_value = {"document": "refreshed"}
    browser.current_candidate_ids = AsyncMock(side_effect=BrowserFailure("列表尚未加载"))
    delivery.deliver.return_value = [
        RunEvent(ex.run.id, "delivery", {"job_id": "j1", "status": "sent", "returned_to_list": True})
    ]
    with pytest.raises(BrowserFailure):
        await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成测试"})
    assert ex.done == {"j1"}
    assert ex.control.pause_requested.is_set() and ex.receipt is None


async def test_current_candidate_snapshot_waits_for_loaded_cards_and_does_not_scroll():
    from boss_say_hello.browser import selectors as dom
    from boss_say_hello.runtime.control import RunControl

    browser = NodriverAdapter()
    browser.bind_control(RunControl())
    browser.poll_attempts = 2
    browser.evaluate = AsyncMock(
        side_effect=[{"cards": []}, {"cards": [{"url": "https://www.zhipin.com/job_detail/loaded.html"}]}]
    )
    assert await browser.current_candidate_ids() == {"loaded"}
    assert all(call.args[1] == dom.CARDS for call in browser.evaluate.call_args_list)
