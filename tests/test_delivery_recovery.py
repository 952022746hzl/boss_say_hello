from unittest.mock import AsyncMock

import pytest

from boss_say_hello.browser.port import ActionResult, SessionInfo
from boss_say_hello.domain import RunEvent
from tests.test_agent_failure_gates import ready_to_send
from tests.test_browser_write import adapter as browser_fixture
from tests.test_delivery import send, setup

adapter = browser_fixture


@pytest.mark.parametrize("stage", ["message", "attachment"])
async def test_partial_known_failure_returns_to_list_without_repeating(stage, snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    browser.outcomes[stage] = ActionResult("failed", "合成控件未就绪")
    browser.return_to_list = AsyncMock(return_value=True)
    run = snapshot(
        config=config(auto_send=True, send_attachment=stage == "attachment", attachment_id="resume1")
    )
    events = await send(service, run, job())
    assert events[0].payload["status"] == "failed"
    assert events[0].payload["returned_to_list"] is True
    browser.return_to_list.assert_awaited_once()
    assert not journal.eligible("a1", "j1")
    await send(service, run, job())
    assert browser.calls.count("message") == 1


async def test_unknown_message_keeps_chat_and_never_releases_contact(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    browser.outcomes["message"] = ActionResult("unknown", "回执未知")
    browser.return_to_list = AsyncMock(return_value=True)
    events = await send(service, snapshot(config=config(auto_send=True)), job())
    assert events[0].payload["status"] == "unknown"
    browser.return_to_list.assert_not_called()
    assert not journal.eligible("a1", "j1")


async def test_preclick_account_change_releases_only_unattempted_contact(
    adapter, snapshot, config, job, storage
):
    browser, journal, service = setup(storage)
    adapter.session = AsyncMock(return_value=SessionInfo("other", True))
    adapter.evaluate = AsyncMock()
    browser.open_contact = adapter.open_contact
    events = await send(service, snapshot(config=config(auto_send=True)), job())
    assert events[0].payload["status"] == "failed"
    adapter.evaluate.assert_not_called()
    assert journal.contact_count("r1") == 0
    assert journal.eligible("a1", "j1")
    assert events[0].payload["stages"]["contact"]["outcome"] == "cancelled"


@pytest.mark.parametrize("returned", [True, False])
async def test_partial_delivery_cleans_refreshed_candidates_or_pauses(
    returned, config, snapshot, job, storage, tmp_path
):
    ex, browser, _, delivery, _ = await ready_to_send(config, snapshot, job, storage, tmp_path)
    ex.jobs["old"] = job(id="old")
    browser.current_candidate_ids = AsyncMock(return_value={"new"})
    delivery.deliver.return_value = [
        RunEvent(
            ex.run.id,
            "delivery",
            {
                "job_id": "j1",
                "status": "failed",
                "returned_to_list": returned,
                "stages": {"contact": {"outcome": "succeeded"}, "message": {"outcome": "failed"}},
            },
        )
    ]
    await ex.execute("send_greeting", {"job_id": "j1", "reason": "合成发送"})
    assert "j1" in ex.done
    if returned:
        assert not ex.jobs and not ex.candidates_need_sync
        assert not ex.control.pause_requested.is_set()
    else:
        assert ex.control.pause_requested.is_set()
        assert ex.candidates_need_sync and ex.receipt is None


async def test_pause_during_return_account_check_prevents_navigation(snapshot, config, storage):
    from boss_say_hello.runtime.control import RunControl

    browser, _, service = setup(storage)
    control = RunControl()

    async def account():
        control.pause_requested.set()
        return SessionInfo("a1", True)

    browser.session = account
    browser.return_to_list = AsyncMock(return_value=True)
    assert await service._return_to_list(snapshot(config=config(auto_send=True)), control) is False
    browser.return_to_list.assert_not_called()


@pytest.mark.parametrize("receipt", [{}, {"clicked": None}, {"clicked": 0}, {"clicked": "false"}])
async def test_malformed_click_receipt_cannot_release_contact(
    adapter, snapshot, config, job, storage, receipt
):
    browser, journal, service = setup(storage)
    adapter.evaluate = AsyncMock(return_value=receipt)
    browser.open_contact = adapter.open_contact
    events = await send(service, snapshot(config=config(auto_send=True)), job())
    assert events[0].payload["status"] == "unknown"
    assert journal.contact_count("r1") == 1 and not journal.eligible("a1", "j1")
