import pytest


def test_verified_preferences_require_selected_target_tag(config):
    from boss_say_hello.browser.preferences import preferences_match

    cfg = config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000)
    snap = {"expectations": [{"label": "Java(深圳)", "selected": True}]}
    assert preferences_match(snap, cfg)
    assert not preferences_match({"expectations": [{"label": "Java(上海)", "selected": True}]}, cfg)
    assert not preferences_match({"expectations": [{"label": "Java(深圳)", "selected": False}]}, cfg)


async def test_preference_action_requires_fresh_snapshot_and_pause_guard():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import BrowserFailure

    browser = NodriverAdapter()
    with pytest.raises(BrowserFailure):
        await browser.preference_action("unknown")


async def test_removed_or_stale_control_is_blocked(config):
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    browser = NodriverAdapter()
    browser.bind_control(SimpleNamespace(pause_requested=Event(), closing=Event()))
    browser.bind_goal(config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000))
    browser.preference_state = {"controls": [{"id": "tag", "kind": "click", "label": "Java(深圳)"}]}
    browser.preference_snapshot = AsyncMock(
        return_value={"controls": [{"id": "new", "kind": "click", "label": "Java(深圳)"}]}
    )
    browser.evaluate = AsyncMock()
    result = await browser.preference_action("save")
    assert result["action_result"]["status"] == "stale_control"
    assert result["controls"][0]["id"] == "new"
    browser.evaluate.assert_not_called()


async def test_candidates_read_cards_without_opening_details(config):
    from unittest.mock import AsyncMock

    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import SessionInfo

    b = NodriverAdapter()
    b.bind_goal(config())
    b.session = AsyncMock(return_value=SessionInfo("a1", True))
    b.evaluate = AsyncMock(
        return_value={
            "cards": [{"url": "https://www.zhipin.com/job_detail/j1.html", "title": "Java"}],
            "ended": True,
        }
    )
    b.read_detail = AsyncMock()
    result = await b.candidate_batch()
    assert result["jobs"][0].id == "j1"
    b.read_detail.assert_not_called()
