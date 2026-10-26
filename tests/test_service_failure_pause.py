import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.ai.agent_client import AgentClient
from boss_say_hello.ai.schemas import AiFailure
from tests.fakes.clock import FakeClock
from tests.test_agent_engine import environment, evaluation_payload, evaluator
from tests.test_rate_limit_engine import close_run, next_pause, observe_pauses


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
async def test_service_http_failure_pauses_first_job_without_spending_other_candidates(
    config, snapshot, job, storage, tmp_path, status
):
    ex, browser, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="j2")], "ended": True}
    browser.refresh_job.side_effect = lambda j: j
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "synthetic-private-details"}})

    ai = AgentClient(ex.run.config, "synthetic-key", httpx.MockTransport(respond))
    pauses = observe_pauses(ex)
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        paused = await next_pause(task, pauses)
        assert len(requests) == 1
        assert "模型服务请求失败" in paused.payload["reason"]
        assert "请结束本轮修改后重新开始" in paused.payload["reason"]
        assert not ex.done and not ex.model_failures
        assert ex.active_job_id() == "j1" and ex.job_steps.get("j1", 0) == 0
        assert not any(event.kind == "finished" for event in events)
        delivery.deliver.assert_not_called()
    finally:
        await close_run(ex, task)
        await ai.close()
    assert events[-1].payload["status"] == "stopped"
    assert events[-1].payload["successes"] == 0


@pytest.mark.parametrize("error", ["connection_failed", "request_timeout", "request_failed"])
async def test_network_failure_keeps_current_job_and_never_counts_as_format_correction(
    config, snapshot, job, storage, tmp_path, error
):
    ex, browser, _, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="j2")], "ended": True}
    ai = SimpleNamespace(evaluate_job=AsyncMock(side_effect=AiFailure(error)))
    pauses = observe_pauses(ex)
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        await next_pause(task, pauses)
        assert ai.evaluate_job.await_count == 1
        assert not ex.done and not ex.model_failures
        assert ex.job_steps.get("j1", 0) == 0
        assert not task.done()
        delivery.deliver.assert_not_called()
    finally:
        await close_run(ex, task)
    assert events[-1].payload["status"] == "stopped"


async def test_failed_search_is_resumable_and_is_not_cached_as_completed(
    config, snapshot, job, storage, tmp_path
):
    ex, _, search, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ex.clock = FakeClock()
    sources = search.search.return_value
    search.search.side_effect = [AiFailure("search_failed", "搜索服务暂时不可用"), sources]
    ai = evaluator("suspected")
    pauses = observe_pauses(ex)
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        await next_pause(task, pauses)
        assert search.search.await_count == 1
        assert "j1" not in ex.company_checked and not ex.search_counts
        assert not ex.sources.get("j1") and not ex.done
        ai.evaluate_job.assert_not_called()
        assert not ex.model_failures and not task.done()
        ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        await close_run(ex, task)
    assert search.search.await_count == 2
    assert "j1" in ex.company_checked and list(ex.search_counts.values()) == [1]
    assert ai.evaluate_job.await_count == 1
    assert events[-1].payload["status"] == "completed"
    delivery.deliver.assert_not_called()


async def test_invalid_json_remains_a_single_bounded_output_correction(
    config, snapshot, job, storage, tmp_path
):
    ex, _, search, delivery, events = environment(config, snapshot, job, storage, tmp_path)
    ai = evaluator()
    ai.evaluate_job.side_effect = [AiFailure("invalid_json"), evaluation_payload("suspected")]
    await AgentEngine(ex, ai).run()
    assert ai.evaluate_job.await_count == 2
    assert ex.model_failures[("j1", "assess_job")] == 1
    assert search.search.await_count == 1
    assert not any(event.kind == "state" and event.payload.get("status") == "paused" for event in events)
    assert events[-1].payload["status"] == "completed"
    delivery.deliver.assert_not_called()


async def test_greeting_service_failure_preserves_assessment_until_network_recovers(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.domain import Greeting

    ex, _, _, delivery, events = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    ex.clock = FakeClock()
    ex.ai.greet.side_effect = [
        AiFailure("connection_failed", "无法连接模型服务"),
        Greeting("您好，我有相关开发经验，希望进一步沟通。"),
    ]
    ai = evaluator()
    pauses = observe_pauses(ex)
    task = asyncio.create_task(AgentEngine(ex, ai).run())
    try:
        await next_pause(task, pauses)
        assessment = ex.assessments["j1"]
        assert ai.evaluate_job.await_count == 1 and ex.ai.greet.await_count == 1
        assert not ex.model_failures and not ex.done
        assert ex.job_steps["j1"] == 1  # Only the successful assessment consumed a model step.
        delivery.deliver.assert_not_called()
        ex.control.pause_requested.clear()
        await asyncio.wait_for(task, 1)
    finally:
        await close_run(ex, task)
    assert ex.assessments["j1"] is assessment
    assert ai.evaluate_job.await_count == 1 and ex.ai.greet.await_count == 2
    delivery.deliver.assert_awaited_once()
    assert events[-1].payload["status"] == "completed"
