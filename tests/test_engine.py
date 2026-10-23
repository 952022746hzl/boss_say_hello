import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.domain import Assessment, Greeting
from boss_say_hello.runtime.control import RunControl
from tests.fakes.browser import FakeBrowser


class FakeClock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
def setup_engine(storage, job):
    from boss_say_hello.delivery.journal import DeliveryJournal
    from boss_say_hello.delivery.service import DeliveryService
    from boss_say_hello.greetings.service import GreetingService
    from boss_say_hello.matching.service import MatchingService
    from boss_say_hello.runs.engine import RunEngine

    def make(jobs=None):
        browser = FakeBrowser(jobs or [job()])
        ai = SimpleNamespace(
            assess=AsyncMock(return_value=Assessment(90, "匹配", ("Python",), (), ())),
            greet=AsyncMock(return_value=Greeting("你好，我有 Python 开发经验。")),
        )
        journal = DeliveryJournal(storage)
        events = []
        clock = FakeClock()
        engine = RunEngine(
            storage,
            browser,
            MatchingService(ai),
            GreetingService(ai),
            DeliveryService(browser, journal),
            journal,
            clock,
            events.append,
        )
        return SimpleNamespace(
            engine=engine,
            browser=browser,
            ai=ai,
            journal=journal,
            events=events,
            clock=clock,
            control=RunControl(),
        )

    return make


async def test_preview_records_greeting_without_contact(setup_engine, snapshot, storage):
    x = setup_engine()
    await x.engine.run(snapshot(), x.control)
    assert not x.browser.calls
    assert any(e.kind == "greeting" for e in storage.events("r1"))


@pytest.mark.parametrize("change", ["failure", "salary", "contacted", "score"])
async def test_ineligible_never_sends(change, setup_engine, snapshot, config, job):
    j = (
        job(salary_raw="面议")
        if change == "salary"
        else job(contact_status="contacted")
        if change == "contacted"
        else job()
    )
    x = setup_engine([j])
    if change == "failure":
        x.ai.assess.side_effect = ValueError("failure")
    if change == "score":
        x.ai.assess.return_value = Assessment(12, "低分", (), (), ())
    await x.engine.run(snapshot(config=config(auto_send=True)), x.control)
    assert x.browser.calls == []
    x.ai.greet.assert_not_called()


async def test_limit_interval_and_snapshot(setup_engine, snapshot, config, job):
    x = setup_engine([job(id=f"j{i}") for i in range(5)])
    times = []
    x.browser.after = lambda s: times.append(x.clock.monotonic()) if s == "contact" else None
    run = snapshot(config=config(auto_send=True, max_contacts=2, interval_seconds=9))
    edited = replace(run.config, max_contacts=99)
    await x.engine.run(run, x.control)
    assert edited.max_contacts == 99 and run.config.max_contacts == 2
    assert len(times) == 2 and times[1] - times[0] >= 9
    assert x.journal.contact_count(run.id) == 2


async def test_pause_resume_keeps_quota(setup_engine, snapshot, config, job):
    x = setup_engine([job(id=f"j{i}") for i in range(3)])
    paused = asyncio.Event()

    def emit(e):
        x.events.append(e)
        if e.kind == "state" and e.payload["status"] == "paused":
            paused.set()

    x.engine.emit = emit
    x.browser.after = lambda s: (
        x.control.pause_requested.set() if s == "contact" and x.browser.calls.count("contact") == 1 else None
    )
    run = snapshot(config=config(auto_send=True, max_contacts=2))
    task = asyncio.create_task(x.engine.run(run, x.control))
    await asyncio.wait_for(paused.wait(), 1)
    assert x.browser.calls == ["contact"]
    x.control.pause_requested.clear()
    await asyncio.wait_for(task, 1)
    assert x.browser.calls == ["contact", "contact", "message"]
    assert x.journal.contact_count(run.id) == 2


async def test_account_switch_pauses(setup_engine, snapshot):
    x = setup_engine()
    x.browser.account = "other"
    paused = asyncio.Event()

    def emit(e):
        x.events.append(e)
        if e.kind == "state" and e.payload["status"] == "paused":
            paused.set()

    x.engine.emit = emit
    task = asyncio.create_task(x.engine.run(snapshot(), x.control))
    await asyncio.wait_for(paused.wait(), 1)
    assert not x.browser.calls
    x.browser.account = "a1"
    x.control.pause_requested.clear()
    await asyncio.wait_for(task, 1)
    assert any(e.kind == "greeting" for e in x.events)
