import asyncio
from types import SimpleNamespace

import httpx
import pytest

from boss_say_hello.ai.schemas import AiRequestInterrupted
from boss_say_hello.runtime.control import RunControl


async def test_gate_serializes_requests_until_the_current_logical_request_finishes():
    from boss_say_hello.ai.request_gate import RequestGate

    gate = RequestGate()
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first():
        async with gate.slot():
            order.append("first")
            entered.set()
            await release.wait()
            order.append("first-finished")

    async def second():
        async with gate.slot():
            order.append("second")

    holder = asyncio.create_task(first())
    await entered.wait()
    waiter = asyncio.create_task(second())
    try:
        await asyncio.sleep(0.02)
        assert order == ["first"]
        release.set()
        await asyncio.wait_for(asyncio.gather(holder, waiter), 1)
        assert order == ["first", "first-finished", "second"]
    finally:
        release.set()
        await asyncio.gather(holder, waiter, return_exceptions=True)


@pytest.mark.parametrize("interruption", ["pause_requested", "closing", "cancel"])
async def test_interrupted_waiter_never_enters_or_releases_the_current_slot(interruption):
    from boss_say_hello.ai.request_gate import RequestGate

    gate = RequestGate()
    control = RunControl()
    entered = []

    async def waiting(label, current_control=None):
        async with gate.slot(control=current_control):
            entered.append(label)

    async with gate.slot():
        waiter = asyncio.create_task(waiting("cancelled", control))
        await asyncio.sleep(0.02)
        if interruption == "cancel":
            waiter.cancel()
            error = asyncio.CancelledError
        else:
            getattr(control, interruption).set()
            error = AiRequestInterrupted
        with pytest.raises(error):
            await asyncio.wait_for(waiter, 0.5)
        successor = asyncio.create_task(waiting("successor"))
        await asyncio.sleep(0.02)
        assert not entered
    await asyncio.wait_for(successor, 0.5)
    assert entered == ["successor"]


@pytest.mark.parametrize("flag", ["pause_requested", "closing"])
async def test_gate_rejects_already_interrupted_control_without_entering(flag):
    from boss_say_hello.ai.request_gate import RequestGate

    gate = RequestGate()
    control = RunControl()
    getattr(control, flag).set()
    with pytest.raises(AiRequestInterrupted):
        async with gate.slot(control=control):
            pytest.fail("interrupted request must not enter")
    async with gate.slot():
        pass


async def test_gate_releases_slot_when_current_request_raises(monkeypatch):
    from boss_say_hello.ai import request_gate
    from boss_say_hello.ai.request_gate import RequestGate

    gate = RequestGate()
    elapsed = 0
    events = []
    monkeypatch.setattr(request_gate, "time", SimpleNamespace(monotonic=lambda: elapsed))
    monkeypatch.setattr(request_gate, "record", lambda event, **fields: events.append((event, fields)))
    with pytest.raises(ValueError, match="synthetic"):
        async with gate.slot():
            elapsed = 4
            raise ValueError("synthetic")
    assert events[-1][0] == "ai.queue.released"
    assert events[-1][1]["wait_ms"] == 0  # Request duration must not inflate queue wait time.
    async with asyncio.timeout(0.5):
        async with gate.slot():
            pass


async def test_recovery_wait_retains_shared_slot_until_retry_finishes(config):
    from boss_say_hello.agent.search import CompanySearch
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.request_gate import RequestGate

    retry_waiting = asyncio.Event()
    release_retry = asyncio.Event()
    now = 0
    calls = []

    async def sleep(seconds):
        nonlocal now
        retry_waiting.set()
        await release_retry.wait()
        now += seconds

    def respond(request):
        kind = "search" if "/tools/" in request.url.path else "model"
        calls.append(kind)
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"Retry-After": "1"}, json={"error": {"code": "rate_limit_exceeded"}}
            )
        return httpx.Response(
            200,
            json={} if kind == "search" else {"choices": [{"message": {"content": "OK"}}]},
        )

    cfg = config(base_url="https://api.moonshot.cn/v1")
    gate = RequestGate()
    ai = AiService(cfg, "synthetic", httpx.MockTransport(respond))
    search = CompanySearch(cfg, "synthetic", httpx.MockTransport(respond))
    ai.request_gate = search.request_gate = gate
    ai.recovery.bind(clock=SimpleNamespace(monotonic=lambda: now, sleep=sleep))
    first = asyncio.create_task(ai.test_connection())
    second = None
    try:
        await asyncio.wait_for(retry_waiting.wait(), 1)
        second = asyncio.create_task(search._post("search_pro", {"text_query": "Synthetic"}))
        await asyncio.sleep(0.02)
        assert calls == ["model"]
        release_retry.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert calls == ["model", "model", "search"]
    finally:
        release_retry.set()
        await asyncio.gather(*([first, second] if second is not None else [first]), return_exceptions=True)
        await ai.close()
        await search.close()
