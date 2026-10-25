from unittest.mock import AsyncMock

from tests.test_browser_write import adapter as browser_fixture

adapter = browser_fixture


async def test_contact_continues_dialog_before_verifying_chat(adapter, job):
    adapter.poll_attempts = 3
    adapter.evaluate = AsyncMock(
        side_effect=[
            {"clicked": True},
            {"matched": False, "can_continue": True},
            {"clicked": True},
            {"matched": True, "count": 0},
        ]
    )
    result = await adapter.open_contact(job())
    assert result.outcome == "succeeded"
    assert '"action": "continue_contact"' in adapter.evaluate.call_args_list[2].args[1]


async def test_pause_after_contact_preserves_unknown_result(adapter, job):
    async def evaluate(tab, code):
        adapter.control.pause_requested.set()
        return {"clicked": True}

    adapter.evaluate = AsyncMock(side_effect=evaluate)
    result = await adapter.open_contact(job())
    assert result.outcome == "unknown"
    assert adapter.evaluate.await_count == 1


async def test_fill_then_recheck_before_click_send(adapter, job):
    adapter.evaluate = AsyncMock(
        side_effect=[
            {"matched": True, "count": 0},
            {"filled": True},
            {"clicked": False, "code": "target_changed"},
        ]
    )
    result = await adapter.send_text(job(), "您好")
    assert result.outcome == "failed"
    assert '"action": "fill_message"' in adapter.evaluate.call_args_list[1].args[1]
    assert '"action": "message_ready"' in adapter.evaluate.call_args_list[2].args[1]
    assert not any('"action": "send_message"' in c.args[1] for c in adapter.evaluate.call_args_list)


async def test_old_pending_same_text_receipt_does_not_confirm_new_send(adapter, job):
    adapter.poll_attempts = 1
    adapter.evaluate = AsyncMock(
        side_effect=[
            {"matched": True, "count": 0, "seen_ids": ["old-pending"]},
            {"filled": True},
            {"ready": True},
            {"ready": True},
            {"clicked": True},
            {"matched": True, "count": 1, "ids": ["old-pending"]},
        ]
    )
    assert (await adapter.send_text(job(), "您好")).outcome == "unknown"
