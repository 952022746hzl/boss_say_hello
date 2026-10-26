import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.ai.agent_client import AgentClient
from boss_say_hello.ai.schemas import AiFailure, AiRequestInterrupted
from boss_say_hello.domain import Greeting
from tests.fakes.clock import FakeClock
from tests.test_agent_engine import assessment_args, environment, evaluation_payload, prepare

LIMIT_MESSAGE = "请求受到限制，请稍后继续本轮"


def observe_pauses(executor):
    pauses = asyncio.Queue()
    original_emit = executor.emit

    def emit(event):
        original_emit(event)
        if event.kind == "state" and event.payload.get("status") == "paused":
            pauses.put_nowait(event)

    executor.emit = emit
    return pauses


async def next_pause(task, pauses):
    waiting = asyncio.create_task(pauses.get())
    try:
        await asyncio.wait({task, waiting}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        assert waiting.done(), "限流应暂停保留本轮，而不是结束任务或继续消耗岗位"
        return waiting.result()
    finally:
        if not waiting.done():
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


async def close_run(executor, task):
    executor.control.closing.set()
    await asyncio.wait_for(task, 1)


async def test_decision_limit_keeps_run_and_job_until_user_stops(config, snapshot, job, storage, tmp_path):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    run = ex.run
    pauses = observe_pauses(ex)
    ai = SimpleNamespace(evaluate_job=AsyncMock(side_effect=AiFailure("rate_limited", LIMIT_MESSAGE)))
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        paused = await next_pause(task, pauses)
        assert LIMIT_MESSAGE in paused.payload["reason"]
        assert not task.done()
        assert ex.run is run
        assert ex.active_job_id() == "j1"
        assert "j1" not in ex.done
        assert not any(event.kind in {"finished", "error"} for event in events)
        delivery.deliver.assert_not_called()
    finally:
        await close_run(ex, task)
    assert events[-1].kind == "finished"
    assert events[-1].payload["status"] == "stopped"
    delivery.deliver.assert_not_called()


async def test_repeated_decision_limits_do_not_spend_job_budget_before_resume(
    config, snapshot, job, storage, tmp_path
):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    pauses = observe_pauses(ex)
    ai = SimpleNamespace(
        evaluate_job=AsyncMock(
            side_effect=[AiFailure("rate_limited", LIMIT_MESSAGE) for _ in range(13)]
            + [evaluation_payload("suspected")]
        )
    )
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        for _ in range(13):
            await next_pause(task, pauses)
            assert ex.job_steps.get("j1", 0) == 0
            assert ex.active_job_id() == "j1"
            assert "j1" not in ex.done
            ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        await close_run(ex, task)
    assert ex.job_steps["j1"] == 1
    assert "j1" in ex.done
    assert events[-1].payload["status"] == "completed"
    assert sum(event.kind == "started" for event in events) == 1
    assert not any(event.kind == "error" for event in events)
    delivery.deliver.assert_not_called()


async def test_draft_limits_preserve_assessment_and_resume_to_one_delivery(
    config, snapshot, job, storage, tmp_path
):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    ex.clock = FakeClock()
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "准备岗位详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "准备公司证据"})
    await ex.execute("assess_job", assessment_args())
    assessment = ex.assessments["j1"]
    ex.ai.greet.side_effect = [
        AiFailure("rate_limited", LIMIT_MESSAGE),
        AiFailure("rate_limited", LIMIT_MESSAGE),
        Greeting("你好，我有 Python 开发经验，希望进一步交流。"),
    ]
    ai = SimpleNamespace()
    pauses = observe_pauses(ex)
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        for _ in range(2):
            await next_pause(task, pauses)
            assert ex.model_failures == {}
            assert "j1" not in ex.done
            assert "j1" not in ex.greetings
            assert ex.assessments["j1"] is assessment
            delivery.deliver.assert_not_called()
            ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        await close_run(ex, task)
    assert events[-1].payload["status"] == "completed"
    assert "j1" in ex.done
    assert ex.model_failures == {}
    delivery.deliver.assert_awaited_once()
    assert delivery.deliver.await_args.args[1].id == "j1"
    assert sum(event.kind == "greeting" for event in events) == 1


@pytest.mark.parametrize(
    "error",
    [AiFailure("rate_limited", LIMIT_MESSAGE), AiRequestInterrupted("合成暂停")],
    ids=["limit", "interrupted"],
)
async def test_company_request_failure_does_not_cache_incomplete_search_as_checked(
    config, snapshot, job, storage, tmp_path, error
):
    ex, _, search, _, _ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    sources = [{"id": "e1", "url": "https://example.com", "text": "Example 自研产品，直接招聘"}]
    search.search.side_effect = [error, sources]
    with pytest.raises(type(error)):
        await ex.execute("search_company", {"job_id": "j1", "reason": "查询遭遇限流"})
    assert "j1" not in ex.company_checked
    assert ex.search_counts == {}
    assert not ex.sources.get("j1")
    recovered = await ex.execute("search_company", {"job_id": "j1", "reason": "恢复后重试查询"})
    assert recovered["status"] == "available"
    assert recovered["sources"][0]["text"] == sources[0]["text"]
    reused = await ex.execute("search_company", {"job_id": "j1", "reason": "复用已完成查询"})
    assert reused["sources"] == recovered["sources"]
    assert search.search.await_count == 2
    assert "j1" in ex.company_checked
    assert list(ex.search_counts.values()) == [1]


async def test_agent_client_retries_transient_429_in_same_decision_without_repeating_page_preparation(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                429,
                json={"error": {"type": "rate_limit_reached_error", "message": "RPM rate limit exceeded"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(evaluation_payload("suspected")),
                        },
                    }
                ]
            },
        )

    ai = AgentClient(ex.run.config, "test-key", httpx.MockTransport(respond))
    try:
        await asyncio.wait_for(AgentEngine(ex, ai).run(), 1)
    finally:
        await ai.close()
    assert events[-1].payload["status"] == "completed"
    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert ex.job_steps == {"j1": 1}
    assert ex.clock.monotonic() > 0
    assert "j1" in ex.done
    browser.preference_snapshot.assert_awaited_once()
    browser.candidate_batch.assert_awaited_once()
    assert not any(event.kind == "error" or event.payload.get("status") == "paused" for event in events)
    delivery.deliver.assert_not_called()


@pytest.mark.parametrize(
    "provider_error,request_limit",
    [
        ({"type": "rate_limit_reached_error", "message": "RPM rate limit exceeded"}, 3),
        ({"code": "insufficient_quota", "message": "quota exhausted"}, 1),
        ({"code": "daily_limit_exceeded", "message": "daily request limit exceeded"}, 1),
    ],
    ids=["temporary-exhausted", "quota", "daily"],
)
async def test_http_limit_pauses_at_retry_budget_and_manual_resume_keeps_same_job(
    config, snapshot, job, storage, tmp_path, provider_error, request_limit
):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    pauses = observe_pauses(ex)
    requests = []
    recovered = False

    def respond(request):
        requests.append(json.loads(request.content))
        if not recovered:
            return httpx.Response(429, json={"error": provider_error})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(evaluation_payload("suspected")),
                        },
                    }
                ]
            },
        )

    ai = AgentClient(ex.run.config, "test-key", httpx.MockTransport(respond))
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        await next_pause(task, pauses)
        assert len(requests) == request_limit
        assert ex.job_steps.get("j1", 0) == 0
        assert ex.active_job_id() == "j1"
        assert "j1" not in ex.done
        assert not any(event.kind in {"finished", "error"} for event in events)
        recovered = True
        ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        await close_run(ex, task)
        await ai.close()
    assert len(requests) == request_limit + 1
    assert events[-1].payload["status"] == "completed"
    assert ex.job_steps == {"j1": 1}
    assert sum(event.kind == "started" for event in events) == 1
    delivery.deliver.assert_not_called()


async def test_stop_during_http_retry_wait_cancels_before_second_request(
    config, snapshot, job, storage, tmp_path
):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    waits = []

    class StopDuringWaitClock(FakeClock):
        async def sleep(self, seconds):
            waits.append(seconds)
            ex.control.closing.set()
            await super().sleep(seconds)

    ex.clock = StopDuringWaitClock()
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            429,
            headers={"retry-after": "30"},
            json={"error": {"type": "rate_limit_reached_error", "message": "RPM rate limit exceeded"}},
        )

    ai = AgentClient(ex.run.config, "test-key", httpx.MockTransport(respond))
    try:
        await asyncio.wait_for(AgentEngine(ex, ai).run(), 1)
    finally:
        await ai.close()
    assert len(requests) == 1
    assert len(waits) == 1
    assert 0 < ex.clock.monotonic() <= 0.1
    assert events[-1].payload["status"] == "stopped"
    assert not any(event.kind == "error" for event in events)
    assert ex.job_steps.get("j1", 0) == 0
    delivery.deliver.assert_not_called()


async def test_pause_during_http_retry_wait_resumes_same_job_without_spending_budget(
    config, snapshot, job, storage, tmp_path
):
    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)

    class PauseOnceClock(FakeClock):
        async def sleep(self, seconds):
            if self.now == 0:
                ex.control.pause_requested.set()
            await super().sleep(seconds)

    ex.clock = PauseOnceClock()
    pauses = observe_pauses(ex)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "30"},
                json={"error": {"type": "rate_limit_reached_error", "message": "RPM rate limit exceeded"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(evaluation_payload("suspected")),
                        },
                    }
                ]
            },
        )

    ai = AgentClient(ex.run.config, "test-key", httpx.MockTransport(respond))
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        await next_pause(task, pauses)
        assert len(requests) == 1
        assert ex.job_steps.get("j1", 0) == 0
        assert ex.active_job_id() == "j1"
        assert not any(event.kind in {"finished", "error"} for event in events)
        ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        await close_run(ex, task)
        await ai.close()
    assert len(requests) == 2
    assert ex.job_steps == {"j1": 1}
    assert events[-1].payload["status"] == "completed"
    delivery.deliver.assert_not_called()
