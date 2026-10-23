import json
from types import SimpleNamespace
from unittest.mock import Mock

from boss_say_hello.domain import RunEvent


def test_agent_settings_roundtrip_and_required_target(qtbot, config, tmp_path):
    from boss_say_hello.storage.settings import ConfigStore
    from boss_say_hello.ui.settings_page import SettingsPage

    controller = SimpleNamespace(save_settings=Mock(), test_connection=Mock())
    page = SettingsPage(controller)
    qtbot.addWidget(page)
    page.set_config(config())
    assert page.agent.outsourcing_check.isChecked()
    page.agent.target_title.setText("Java")
    page.agent.outsourcing_check.setChecked(False)
    page.agent.limits["max_candidates"].setValue(8)
    page.agent.filters["学历"].setCurrentText("本科")
    store = ConfigStore(tmp_path)
    store.save(page.read_config())
    page.set_config(store.load())
    assert page.agent.target_title.text() == "Java"
    assert not page.agent.outsourcing_check.isChecked()
    assert page.read_config().max_candidates == 8
    assert page.read_config().native_filters["学历"] == "本科"
    page.agent.target_title.clear()
    # API test is independent from job target requirements.
    page.read_config(ready=True)
    import pytest

    with pytest.raises(ValueError):
        page.read_run_config()
    assert page.agent.target_title.property("invalid")


def test_tool_and_company_history_visible_and_exported(qtbot, storage, snapshot, tmp_path):
    from boss_say_hello.ui.presentation import details
    from boss_say_hello.ui.run_page import RunPage

    page = RunPage(SimpleNamespace(request_pause=Mock(), request_resume=Mock()), lambda: None)
    qtbot.addWidget(page)
    page.on_event(RunEvent("r1", "started", {"mode": "preview"}))
    action = RunEvent(
        "r1", "tool_action", {"tool": "search_company", "reason": "核对外包嫌疑", "job_id": "j1"}
    )
    page.on_event(action)
    assert "核对外包嫌疑" in page.steps.toPlainText()
    source = RunEvent(
        "r1",
        "company_sources",
        {
            "job_id": "j1",
            "company": {"name": "合成公司"},
            "sources": [
                {
                    "id": "e1",
                    "url": "https://example.com",
                    "text": "自研业务",
                    "queried_at": "today",
                    "title": "官网",
                }
            ],
        },
    )
    bg = RunEvent(
        "r1",
        "background",
        {
            "job_id": "j1",
            "score": 60,
            "status": "excluded",
            "outsourcing": "suspected",
            "reason": "有外包业务",
            "source_ids": ["e1"],
        },
    )
    assert "https://example.com" in details([source, bg])
    storage.save_run(snapshot())
    for e in [action, source, bg]:
        storage.append_event(e)
    storage.export_history(tmp_path / "jobs.json")
    row = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))["jobs"][0]
    assert row["encounters"][0]["background"]["status"] == "excluded"
    assert row["encounters"][0]["steps"][0]["tool"] == "search_company"
