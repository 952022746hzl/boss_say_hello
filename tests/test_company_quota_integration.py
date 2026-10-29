from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.diagnostics import EventLog
from boss_say_hello.domain import AppConfig
from boss_say_hello.storage.settings import ConfigStore
from tests.test_agent_engine import environment, evaluator
from tests.test_delivery import send, setup


def test_company_daily_limit_defaults_and_persists(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    store = ConfigStore(tmp_path)
    assert store.load().max_company_daily_contacts == 1
    store.save(replace(AppConfig(), max_company_daily_contacts=3))
    assert ConfigStore(tmp_path).load().max_company_daily_contacts == 3


@pytest.mark.parametrize("value", [0, -1, True, 1.5, 10001])
def test_company_daily_limit_rejects_invalid_config(value):
    with pytest.raises(ValueError):
        replace(AppConfig(), max_company_daily_contacts=value).validate()


def test_company_daily_limit_ui_load_edit_and_save(qtbot):
    from boss_say_hello.ui.settings_page import SettingsPage

    controller = SimpleNamespace(save_settings=Mock())
    page = SettingsPage(controller)
    qtbot.addWidget(page)
    assert page.max_company_daily_contacts.value() == 1
    page.set_config(replace(AppConfig(), max_company_daily_contacts=3))
    assert page.max_company_daily_contacts.value() == 3
    page.max_company_daily_contacts.setValue(2)
    assert page.dirty
    page.save()
    assert controller.save_settings.call_args.args[0].max_company_daily_contacts == 2


async def test_company_limit_skips_before_any_paid_ai_call(
    config, snapshot, job, storage, tmp_path, monkeypatch
):
    ex, _, search, delivery, events = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    reason = "该公司今日成功联系已达上限 1 个岗位"
    monkeypatch.setattr(ex.journal, "company_limit_reason", lambda *args: reason, raising=False)
    ai = evaluator()
    log = EventLog(tmp_path / "logs")
    try:
        with log.bind():
            await AgentEngine(ex, ai).run()
        assert any(reason in row["message"] for row in log.tail() if row["event"] == "company.quota.blocked")
    finally:
        log.close()
    search.search.assert_not_called()
    ai.evaluate_job.assert_not_called()
    ex.ai.greet.assert_not_called()
    delivery.deliver.assert_not_called()
    assert any(event.kind == "check" and reason in event.payload.get("reasons", []) for event in events)


async def test_preview_is_not_blocked_by_company_daily_limit(
    config, snapshot, job, storage, tmp_path, monkeypatch
):
    ex, _, _, _, _ = environment(config, snapshot, job, storage, tmp_path, auto_send=False)
    checker = Mock(return_value="公司今日上限")
    monkeypatch.setattr(ex.journal, "company_limit_reason", checker, raising=False)
    ai = evaluator()
    await AgentEngine(ex, ai).run()
    ai.evaluate_job.assert_awaited_once()
    checker.assert_not_called()


async def test_delivery_explains_company_limit_and_never_opens_contact(
    config, snapshot, job, storage, monkeypatch
):
    browser, journal, service = setup(storage)
    reason = "该公司今日成功联系已达上限 1 个岗位"
    monkeypatch.setattr(journal, "company_limit_reason", lambda *args: reason, raising=False)
    result = await send(service, snapshot(config=config(auto_send=True)), job())
    assert not browser.calls
    assert result[-1].payload["reason"] == reason


@pytest.mark.parametrize("paused", [False, True])
async def test_delivery_releases_company_reservation_after_work(
    config, snapshot, job, storage, monkeypatch, paused
):
    from boss_say_hello.runtime.control import RunControl

    browser, journal, service = setup(storage)
    released = Mock()
    monkeypatch.setattr(journal, "release_company_reservation", released, raising=False)
    control = RunControl()
    if paused:
        browser.after = lambda stage: control.pause_requested.set() if stage == "contact" else None
    await send(service, snapshot(config=config(auto_send=True)), job(), control)
    released.assert_called_once()


async def test_round_moves_on_to_other_company_without_assessing_second_role(
    setup_agent, snapshot, agent_config, job
):
    run = snapshot(config=agent_config(auto_send=True, max_contacts=2))
    jobs = [
        job(id="first", company_id="co1"),
        job(id="same", company_id="co1"),
        job(id="different", company_id="co2"),
    ]
    app = setup_agent(run, jobs)
    await app.engine.run()
    assert app.journal.success_count(run.id) == 2
    assert app.journal.eligible(run.account_id, "same")
    assert app.ai.evaluate_job.await_count == 2
    assert app.ai.greet.await_count == 2
    assert app.events[-1].payload["status"] == "limit_reached"
    assert any(
        e.kind == "check"
        and e.payload.get("job_id") == "same"
        and "每日上限" in "".join(e.payload.get("reasons", []))
        for e in app.events
    )
