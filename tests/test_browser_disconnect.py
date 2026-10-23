from types import SimpleNamespace
from unittest.mock import AsyncMock


async def test_dead_browser_session_does_not_poll_stale_tab():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    browser = NodriverAdapter()
    browser.browser = SimpleNamespace(_process=SimpleNamespace(returncode=0))
    browser.list_tab = object()
    browser.evaluate = AsyncMock(return_value={"logged_in": True, "account_id": "stale"})
    session = await browser.session()
    assert session.logged_in is False
    browser.evaluate.assert_not_called()
    assert browser.browser is None and browser.list_tab is None


async def test_open_replaces_dead_browser(tmp_path, monkeypatch):
    import nodriver

    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    old = SimpleNamespace(_process=SimpleNamespace(returncode=0))
    replacement = SimpleNamespace(get=AsyncMock(return_value=object()))
    start = AsyncMock(return_value=replacement)
    monkeypatch.setattr(nodriver, "start", start)
    browser = NodriverAdapter()
    browser.browser = old
    browser.list_tab = object()
    await browser.open(tmp_path)
    start.assert_awaited_once()
    assert browser.browser is replacement
