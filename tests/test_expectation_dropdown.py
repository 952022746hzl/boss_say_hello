from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QComboBox

TAGS = [
    {"label": "Java(深圳)", "title": "Java", "city": "深圳"},
    {"label": "Java(北京)", "title": "Java", "city": "北京"},
]


def test_existing_labels_ignore_recommendation_and_deduplicate():
    from boss_say_hello.browser.preferences import parse_expectations

    assert parse_expectations(["推荐", "Java（深圳）", "Java(深圳)", "Java(北京)", "新增期望", "无城市"]) == [
        {"label": "Java（深圳）", "title": "Java", "city": "深圳"},
        TAGS[1],
    ]


def test_dropdown_selects_title_and_city_and_restores_saved_choice(qtbot, config, tmp_path):
    from boss_say_hello.storage.settings import ConfigStore
    from boss_say_hello.ui.settings_page import SettingsPage

    controller = SimpleNamespace(save_settings=Mock(), test_connection=Mock())
    page = SettingsPage(controller)
    qtbot.addWidget(page)
    page.set_config(config(target_title="Java", cities=("深圳",), salary_min=20000, salary_max=30000))
    assert isinstance(page.agent.target_title, QComboBox)
    assert not page.agent.target_title.isEditable()
    # Saved values survive startup, but cannot be used until verified against the account.
    assert page.read_config().target_title == "Java"
    with pytest.raises(ValueError, match="标签"):
        page.read_run_config()
    page.agent.set_expectations(TAGS)
    assert page.agent.target_title.currentText() == "Java(深圳)"
    page.agent.target_title.setCurrentIndex(1)
    assert page.cities.text() == "北京"
    assert page.read_run_config().target_title == "Java"
    assert page.read_run_config().cities == ("北京",)
    assert page.agent.outsourcing_check.isChecked()
    store = ConfigStore(tmp_path)
    store.save(page.read_config())
    page.set_config(store.load())
    page.agent.set_expectations(list(reversed(TAGS)))
    assert page.agent.target_title.currentText() == "Java(北京)"
    assert page.read_run_config().cities == ("北京",)


def test_refresh_never_substitutes_first_tag_or_erases_saved_goal(qtbot, config):
    from boss_say_hello.ui.settings_page import SettingsPage

    page = SettingsPage(SimpleNamespace(save_settings=Mock(), test_connection=Mock()))
    qtbot.addWidget(page)
    page.set_config(config(target_title="Python", cities=("杭州",), salary_min=20000, salary_max=30000))
    page.agent.set_expectations(TAGS)
    assert page.agent.target_title.currentIndex() == -1
    assert page.read_config().target_title == "Python"
    assert page.read_config().cities == ("杭州",)
    with pytest.raises(ValueError, match="标签"):
        page.read_run_config()
    page.agent.target_title.setCurrentIndex(0)
    page.agent.set_expectations([])
    with pytest.raises(ValueError, match="标签"):
        page.read_run_config()
    assert page.read_config().target_title == "Java"


async def test_login_publishes_tags_and_refresh_is_blocked_during_run(tmp_path):
    from boss_say_hello.runtime.backend import Backend
    from tests.fakes.browser import FakeBrowser

    class Browser(FakeBrowser):
        async def existing_expectations(self):
            self.calls.append("read_tags")
            return TAGS

    events = []
    backend = Backend(events.append, root=tmp_path, browser=Browser())
    await backend.initialize()
    try:
        await backend.check_login()
        updates = [e.payload for e in events if e.kind == "expectations"]
        assert updates and updates[-1] == {"account_id": "a1", "items": TAGS}
        backend.busy = True
        with pytest.raises(ValueError, match="结束本轮"):
            await backend.refresh_expectations()
        assert backend.browser.calls == ["read_tags"]
        backend.busy = False
        backend.browser.account = None
        await backend.check_login()
        assert [e.payload for e in events if e.kind == "expectations"][-1]["items"] == []
    finally:
        await backend.close()


def test_login_events_and_refresh_button_populate_real_window(qtbot, tmp_path, monkeypatch):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread
    from boss_say_hello.ui.main_window import MainWindow
    from tests.fakes.browser import FakeBrowser

    class Browser(FakeBrowser):
        async def existing_expectations(self):
            return TAGS if self.account == "a1" else [TAGS[1]]

    browser = Browser()
    monkeypatch.setattr("boss_say_hello.runtime.backend.NodriverAdapter", lambda: browser)
    monkeypatch.setattr("boss_say_hello.runtime.backend.data_root", lambda: tmp_path)
    worker = WorkerThread()
    worker.start()
    controller = Controller(worker)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    try:
        controller.check_login()
        qtbot.waitUntil(lambda: window.settings.agent.target_title.count() == 2)
        window.settings.agent.target_title.setCurrentIndex(0)
        assert window.settings.cities.text() == "深圳"
        browser.account = "a2"
        window.settings.agent.refresh_tags.click()
        qtbot.waitUntil(lambda: window.settings.agent.target_title.count() == 1)
        assert window.settings.agent.target_title.currentIndex() == -1
        assert window.settings.agent.target_title.itemText(0) == "Java(北京)"
        assert window.settings.agent.outsourcing_check.isChecked()
    finally:
        window.settings.auto_save_timer.stop()
        window.settings.dirty = False
        controller.close()
        assert worker.wait(3000)
