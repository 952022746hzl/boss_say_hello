import asyncio

import pytest

from boss_say_hello.browser.port import ActionResult
from boss_say_hello.domain import Assessment, Greeting
from boss_say_hello.runtime.control import RunControl
from tests.fakes.browser import FakeBrowser


def setup(storage, jobs=()):
    from boss_say_hello.delivery.journal import DeliveryJournal
    from boss_say_hello.delivery.service import DeliveryService

    browser = FakeBrowser(jobs)
    journal = DeliveryJournal(storage)
    return browser, journal, DeliveryService(browser, journal)


async def send(service, run, job, control=None):
    return await service.deliver(
        run,
        job,
        Assessment(80, "good", (), (), ()),
        Greeting("你好，我有相关的 Python 项目经历。"),
        control or RunControl(),
    )


async def test_preview_has_zero_contact_actions(snapshot, job, storage):
    browser, journal, service = setup(storage)
    events = await send(service, snapshot(), job())
    assert browser.calls == [] and journal.contact_count("r1") == 0
    assert events[-1].payload["status"] == "preview"


async def test_text_delivery_only_contacts_and_sends_message(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    await send(service, snapshot(config=config(auto_send=True)), job())
    assert browser.calls == ["contact", "message"]
    assert journal.contact_count("r1") == 1


async def test_unknown_blocks_retry_and_consumes_slot(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    browser.outcomes["message"] = ActionResult("unknown", "timeout")
    run = snapshot(config=config(auto_send=True))
    events = await send(service, run, job())
    await send(service, run, job())
    assert browser.calls == ["contact", "message"] and journal.contact_count("r1") == 1
    assert events[-1].payload["status"] == "unknown"


async def test_navigation_failure_keeps_success(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    browser.returned = False
    events = await send(service, snapshot(config=config(auto_send=True)), job())
    assert events[-1].payload["status"] == "sent"
    assert events[-1].payload["returned_to_list"] is False


@pytest.mark.parametrize("stage,expected", [("contact", ["contact"]), ("message", ["contact", "message"])])
async def test_pause_between_steps(snapshot, config, job, storage, stage, expected):
    browser, journal, service = setup(storage)
    control = RunControl()
    browser.after = lambda s: control.pause_requested.set() if s == stage else None
    await send(
        service,
        snapshot(config=config(auto_send=True)),
        job(),
        control,
    )
    assert browser.calls == expected


async def test_changed_job_requires_new_assessment(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    browser.changed_job = job(text="different role")
    events = await send(service, snapshot(config=config(auto_send=True)), job())
    assert browser.calls == [] and events[-1].payload["status"] == "changed"


async def test_intent_write_failure_prevents_action(snapshot, config, job, storage, monkeypatch):
    browser, journal, service = setup(storage)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(journal, "begin_stage", fail)
    with pytest.raises(OSError):
        await send(service, snapshot(config=config(auto_send=True)), job())
    assert browser.calls == []


async def test_cancellation_records_unknown(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    browser.outcomes["message"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await send(service, snapshot(config=config(auto_send=True)), job())
    assert not journal.eligible("a1", "j1")
    assert (
        storage.db.execute("SELECT outcome FROM delivery_attempts WHERE stage='message'").fetchone()[0]
        == "unknown"
    )
