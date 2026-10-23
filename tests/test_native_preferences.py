import pytest


def test_verified_preferences_require_all_native_conditions(config):
    from boss_say_hello.browser.preferences import preferences_match

    cfg = config(
        target_title="Java",
        cities=("深圳",),
        salary_min=18000,
        salary_max=25000,
        native_filters={"经验": "3-5年", "薪资": "20-50K"},
    )
    snap = {
        "expectations": [{"label": "Java(深圳)", "selected": True}],
        "city": "深圳",
        "filters": [{"field": "经验", "selected": ["3-5年"]}, {"field": "薪资", "selected": ["20-50K"]}],
    }
    assert preferences_match(snap, cfg)
    assert not preferences_match({**snap, "city": "上海"}, cfg)
    assert not preferences_match({**snap, "filters": []}, cfg)
    assert not preferences_match({**snap, "expectations": [{"label": "推荐", "selected": True}]}, cfg)


async def test_preference_action_requires_fresh_snapshot_and_pause_guard():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import BrowserFailure

    browser = NodriverAdapter()
    with pytest.raises(BrowserFailure):
        await browser.preference_action("click", "unknown")


async def test_duplicate_expectation_save_is_blocked(config):
    from threading import Event
    from types import SimpleNamespace

    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import BrowserFailure

    browser = NodriverAdapter()
    browser.bind_control(SimpleNamespace(pause_requested=Event(), closing=Event()))
    browser.bind_goal(config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000))
    browser.preference_state = {
        "adding": True,
        "controls": [{"id": "save", "kind": "click", "label": "完成"}],
        "expectations": [{"label": "Java(深圳)", "selected": False}],
        "form_values": {
            "期望职位": "Java",
            "工作城市": "深圳",
            "薪资下限": "18k",
            "薪资上限": "25k",
            "求职类型": "全职",
        },
    }
    with pytest.raises(BrowserFailure, match="已有"):
        await browser.preference_action("click", "save")


def test_goal_salary_cannot_be_silently_rewritten(config):
    from boss_say_hello.browser.preferences import expectation_form_matches

    cfg = config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000)
    assert expectation_form_matches(
        {"期望职位": "Java", "工作城市": "深圳", "薪资下限": "18k", "薪资上限": "25k", "求职类型": "全职"},
        cfg,
    )
    assert not expectation_form_matches(
        {"期望职位": "Java", "工作城市": "深圳", "薪资下限": "15k", "薪资上限": "25k", "求职类型": "全职"},
        cfg,
    )


def test_salary_requires_native_bucket_even_without_explicit_filter(config):
    from boss_say_hello.browser.preferences import preferences_match

    cfg = config(target_title="Java", cities=("深圳",), salary_min=20000, salary_max=30000)
    snap = {"expectations": [{"label": "Java(深圳)", "selected": True}], "city": "深圳", "filters": []}
    assert not preferences_match(snap, cfg)
    snap["filters"] = [{"field": "薪资", "selected": ["20-50K"]}]
    assert preferences_match(snap, cfg)


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
