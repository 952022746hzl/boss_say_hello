from unittest.mock import AsyncMock

import pytest

from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
from boss_say_hello.browser.port import ActionBlocked, SessionInfo
from boss_say_hello.runtime.control import RunControl


@pytest.fixture
def adapter():
    a = NodriverAdapter()
    a.detail_tab = object()
    a.session = AsyncMock(return_value=SessionInfo("a1", True))
    a.evaluate = AsyncMock()
    a.bind_control(RunControl())
    return a


async def test_pause_before_action_has_no_dom_call(adapter, job):
    adapter.control.pause_requested.set()
    with pytest.raises(ActionBlocked):
        await adapter.open_contact(job())
    adapter.evaluate.assert_not_called()


async def test_account_change_has_no_click(adapter, job):
    adapter.session.return_value = SessionInfo("other", True)
    assert (await adapter.open_contact(job())).outcome == "failed"
    adapter.evaluate.assert_not_called()


async def test_click_is_not_delivery_evidence(adapter, job):
    adapter.poll_attempts = 1
    adapter.evaluate.side_effect = [{"clicked": True}, {"matched": False}]
    result = await adapter.open_contact(job())
    assert result.outcome == "unknown"


async def test_verified_new_outgoing_message(adapter, job):
    adapter.evaluate.side_effect = [
        {"matched": True, "count": 2},
        {"clicked": True},
        {"matched": True, "count": 3},
    ]
    result = await adapter.send_text(job(), "你好")
    assert result.outcome == "succeeded"


async def test_wrong_conversation_never_sends(adapter, job):
    adapter.evaluate.return_value = {"matched": False}
    assert (await adapter.send_text(job(), "你好")).outcome == "failed"
    assert adapter.evaluate.await_count == 1


async def test_close_waits_for_owned_browser_process():
    from unittest.mock import Mock

    process = Mock(wait=AsyncMock())
    a = NodriverAdapter()
    a.browser = Mock(_process=process, aclose=AsyncMock())
    await a.close()
    process.wait.assert_awaited_once()
