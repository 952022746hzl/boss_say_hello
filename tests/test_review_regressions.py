import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.browser.port import ActionResult
from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.delivery.service import DeliveryService
from boss_say_hello.domain import Assessment, Greeting
from boss_say_hello.runtime.backend import Backend
from boss_say_hello.runtime.control import RunControl
from tests.fakes.browser import FakeBrowser


@pytest.mark.parametrize("persistent", [False, True])
async def test_endpoint_change_never_reuses_other_provider_key(tmp_path, config, monkeypatch, persistent):
    b = Backend(lambda e: None, root=tmp_path, browser=FakeBrowser())
    await b.initialize()
    saved = {}
    b.credentials = SimpleNamespace(save=lambda k, v: saved.update({k: v}), get=lambda k: saved.get(k))
    a = config(base_url="https://a.invalid/v1")
    other = config(base_url="https://b.invalid/v1")
    # Existing public signature uses credential_id; the fix binds it to config's endpoint.
    await b.save_settings(a)
    if persistent:
        await b.save_credential(a.credential_id, "secret-A")
        b.secrets.clear()
    else:
        await b.use_credential(a.credential_id, "secret-A")
    captured = []

    def factory(c, key):
        captured.append(key)
        return SimpleNamespace(test_connection=AsyncMock(), close=AsyncMock())

    monkeypatch.setattr("boss_say_hello.runtime.backend.AiService", factory)
    await b.test_connection(other, "")
    assert captured == [""]
    await b.close()


async def test_pause_before_any_contact_preserves_current_job(setup_engine, snapshot, config):
    x = setup_engine()
    original = x.browser.refresh_job
    paused = asyncio.Event()
    once = True

    async def refresh(job):
        nonlocal once
        if once:
            x.control.pause_requested.set()
            once = False
        return await original(job)

    def emit(e):
        if e.kind == "state" and e.payload["status"] == "paused":
            paused.set()

    x.browser.refresh_job = refresh
    x.engine.emit = emit
    task = asyncio.create_task(x.engine.run(snapshot(config=config(auto_send=True)), x.control))
    await asyncio.wait_for(paused.wait(), 1)
    assert not x.browser.calls
    x.control.pause_requested.clear()
    await asyncio.wait_for(task, 1)
    assert x.browser.calls == ["contact", "message"]


async def test_restart_history_contains_persisted_steps(tmp_path, snapshot, job):
    b = Backend(lambda e: None, root=tmp_path, browser=FakeBrowser())
    await b.initialize()
    b.store.save_run(snapshot())
    attempt = b.journal.reserve(snapshot(), job())
    b.journal.begin_stage(attempt, "contact")
    b.journal.finish_stage(attempt, "contact", ActionResult("succeeded", "联系已核实"))
    b.journal.begin_stage(attempt, "message")
    await b.close()
    events = []
    reopened = Backend(events.append, root=tmp_path, browser=FakeBrowser())
    await reopened.initialize()
    await reopened.load_history("r1")
    history = events[-1].payload["events"]
    assert any(e.payload.get("stages", {}).get("message", {}).get("outcome") == "unknown" for e in history)
    assert any(e.payload.get("stages", {}).get("contact", {}).get("outcome") == "succeeded" for e in history)
    await reopened.close()


async def test_new_default_message_requires_visible_ack(storage, snapshot, config, job):
    browser = FakeBrowser()
    browser.behavior = "default_message"
    control = RunControl()
    service = DeliveryService(browser, DeliveryJournal(storage))
    result = await service.deliver(
        snapshot(config=config(auto_send=True)),
        job(),
        Assessment(90, "匹配", (), (), ()),
        Greeting("你好"),
        control,
    )
    assert not browser.calls
    assert result[-1].payload["status"] == "needs_default_ack"


async def test_default_message_resume_keeps_current_job(setup_engine, snapshot, config):
    x = setup_engine()
    x.browser.behavior = "default_message"
    visible = asyncio.Event()

    def emit(event):
        if event.kind == "state" and event.payload.get("requires_default_ack"):
            visible.set()

    x.engine.emit = emit
    task = asyncio.create_task(x.engine.run(snapshot(config=config(auto_send=True)), x.control))
    await asyncio.wait_for(visible.wait(), 1)
    assert not x.browser.calls and x.journal.contact_count("r1") == 0
    x.control.default_message_acknowledged.set()
    x.control.pause_requested.clear()
    await asyncio.wait_for(task, 1)
    assert x.browser.calls == ["contact", "message"]


def test_controller_cannot_ack_before_warning_is_displayed(qtbot):
    from boss_say_hello.domain import RunEvent
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread

    worker = WorkerThread()
    worker.start()
    c = Controller(worker)
    try:
        c.control.default_message_required.set()
        c.control.pause_requested.set()
        c.request_resume()
        assert c.control.pause_requested.is_set() and not c.control.default_message_acknowledged.is_set()
        c.receive(RunEvent("r", "state", {"status": "paused", "requires_default_ack": True}))
        c.request_resume()
        assert c.control.default_message_acknowledged.is_set() and not c.control.pause_requested.is_set()
    finally:
        c.close()
        assert worker.wait(3000)
