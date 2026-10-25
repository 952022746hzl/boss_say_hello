import json
from unittest.mock import AsyncMock

import pytest

from tests.test_browser_write import adapter as browser_fixture

adapter = browser_fixture


@pytest.fixture(autouse=True)
def no_poll_delay(monkeypatch):
    monkeypatch.setattr("boss_say_hello.browser.nodriver_adapter.asyncio.sleep", AsyncMock())


def operations(adapter):
    return [
        json.loads(c.args[1].split("const a = ", 1)[1].split(";", 1)[0])["action"]
        for c in adapter.evaluate.call_args_list
    ]


async def test_waits_for_button_then_sends_only_once(adapter, job):
    adapter.evaluate.side_effect = [
        {"matched": True},
        {"filled": True},
        {"ready": False, "code": "send_control_not_ready"},
        {"ready": True},
        {"ready": True},
        {"clicked": True},
        {"matched": True, "ids": ["new"]},
    ]
    assert (await adapter.send_text(job(), "您好")).outcome == "succeeded"
    assert operations(adapter).count("send_message") == 1
    assert operations(adapter).count("message_ready") == 3


async def test_chat_initialization_clears_draft_refills_at_most_once(adapter, job):
    adapter.evaluate.side_effect = [
        {"matched": True},
        {"filled": True},
        {"ready": False, "code": "draft_empty"},
        {"filled": True},
        {"ready": True},
        {"ready": True},
        {"clicked": True},
        {"matched": True, "ids": ["new"]},
    ]
    assert (await adapter.send_text(job(), "您好")).outcome == "succeeded"
    assert operations(adapter).count("fill_message") == 2
    assert operations(adapter).count("send_message") == 1


@pytest.mark.parametrize("code", ["draft_changed", "target_changed"])
async def test_changed_draft_or_target_never_refills_or_sends(adapter, job, code):
    adapter.evaluate.side_effect = [{"matched": True}, {"filled": True}, {"ready": False, "code": code}]
    result = await adapter.send_text(job(), "您好")
    assert result.outcome == "failed"
    assert "send_message" not in operations(adapter)
    assert operations(adapter).count("fill_message") == 1


async def test_second_empty_draft_stops_without_retry_loop(adapter, job):
    adapter.evaluate.side_effect = [
        {"matched": True},
        {"filled": True},
        {"code": "draft_empty"},
        {"filled": True},
        {"code": "draft_empty"},
    ]
    assert (await adapter.send_text(job(), "您好")).outcome == "failed"
    assert operations(adapter).count("fill_message") == 2
    assert "send_message" not in operations(adapter)


async def test_disabled_button_times_out_without_click(adapter, job):
    adapter.editor_poll_attempts = 3
    adapter.evaluate.side_effect = [{"matched": True}, {"filled": True}] + [
        {"code": "send_control_not_ready"}
    ] * 3
    result = await adapter.send_text(job(), "您好")
    assert result.outcome == "failed" and "按钮" in result.detail
    assert operations(adapter).count("message_ready") == 3
    assert "send_message" not in operations(adapter)


async def test_account_change_after_wait_prevents_send(adapter, job):
    from boss_say_hello.browser.port import SessionInfo

    adapter.session.side_effect = [SessionInfo("a1", True), SessionInfo("other", True)]
    adapter.evaluate.side_effect = [{"matched": True}, {"filled": True}, {"ready": True}, {"ready": True}]
    assert (await adapter.send_text(job(), "您好")).outcome == "failed"
    assert "send_message" not in operations(adapter)


async def test_pause_during_wait_never_clicks_send(adapter, job):
    from boss_say_hello.browser.port import ActionBlocked

    async def evaluate(tab, code):
        if '"action": "observe"' in code:
            return {"matched": True}
        if '"action": "fill_message"' in code:
            return {"filled": True}
        adapter.control.pause_requested.set()
        return {"ready": True}

    adapter.evaluate.side_effect = evaluate
    with pytest.raises(ActionBlocked):
        await adapter.send_text(job(), "您好")
    assert "send_message" not in operations(adapter)


async def test_manual_same_text_send_during_wait_is_unknown_and_never_refilled(adapter, job):
    adapter.evaluate.side_effect = [
        {"matched": True, "seen_ids": ["old"]},
        {"filled": True},
        {"code": "matching_message_appeared"},
    ]
    result = await adapter.send_text(job(), "您好")
    assert result.outcome == "unknown" and "重复" in result.detail
    assert operations(adapter).count("fill_message") == 1
    assert "send_message" not in operations(adapter)
    for call in adapter.evaluate.call_args_list[1:]:
        assert '"baseline_ids": ["old"]' in call.args[1]
