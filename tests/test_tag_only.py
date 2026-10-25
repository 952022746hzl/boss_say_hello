from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock


def test_selected_tag_suffices_without_native_city_or_salary_filters(config):
    from boss_say_hello.browser.preferences import preferences_match

    goal = config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000)
    state = {"expectations": [{"label": "Java(深圳)", "selected": True}], "city": "城市", "filters": []}
    assert preferences_match(state, goal)
    assert not preferences_match({"expectations": [{"label": "Java(北京)", "selected": True}]}, goal)


def test_saved_native_filters_are_dropped_but_other_settings_preserved(config):
    from boss_say_hello.domain import config_from_dict

    old = asdict(config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000))
    old["native_filters"] = {"学历": "本科", "薪资": "20-50K"}
    updated = config_from_dict(old)
    assert "native_filters" not in asdict(updated)
    assert updated.target_title == "Java" and updated.cities == ("深圳",)
    assert updated.outsourcing_check


def test_ui_allows_cross_bucket_salary_after_selecting_tag(qtbot, config):
    from PyQt6.QtWidgets import QLabel

    from boss_say_hello.ui.settings_page import SettingsPage

    page = SettingsPage(SimpleNamespace(save_settings=Mock(), test_connection=Mock()))
    qtbot.addWidget(page)
    page.set_config(config(target_title="Java", cities=("深圳",), salary_min=18000, salary_max=25000))
    page.agent.set_expectations([{"title": "Java", "city": "深圳", "label": "Java(深圳)"}])
    assert page.read_run_config().salary_max == 25000
    assert not any("BOSS 原生" in w.text() for w in page.findChildren(QLabel))
