import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


def environment(config, snapshot, job, storage, tmp_path, **overrides):
    from boss_say_hello.agent.tools import ToolExecutor
    from boss_say_hello.delivery.journal import DeliveryJournal
    from boss_say_hello.runs.control import Clock
    from boss_say_hello.storage.companies import CompanyCache

    cfg = config(
        target_title="Python engineer", cities=("北京",), salary_min=20000, salary_max=30000, **overrides
    )
    run = snapshot(config=cfg)
    browser = SimpleNamespace(
        session=AsyncMock(return_value=SimpleNamespace(account_id="a1", logged_in=True)),
        bind_control=lambda c: None,
        bind_goal=lambda c: None,
        preference_snapshot=AsyncMock(
            return_value={
                "document": "doc",
                "expectations": [{"label": "Python engineer(北京)", "selected": True}],
                "city": "北京",
                "filters": [],
            }
        ),
        verify_preferences=AsyncMock(return_value=True),
        candidate_batch=AsyncMock(return_value={"jobs": [job()], "ended": True}),
        refresh_job=AsyncMock(return_value=job()),
        company_info=AsyncMock(
            return_value={
                "id": "co1",
                "name": "Example",
                "city": "北京",
                "url": "https://example.com",
                "text": "产品研发",
            }
        ),
    )
    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "id": "e1",
                    "url": "https://example.com",
                    "text": "Example 自研产品，直接招聘",
                    "title": "官网",
                }
            ]
        )
    )
    ai = SimpleNamespace(greet=AsyncMock(return_value=SimpleNamespace()))
    delivery = SimpleNamespace(deliver=AsyncMock(return_value=[]))
    control = SimpleNamespace(pause_requested=Event(), closing=Event())
    events = []
    executor = ToolExecutor(
        run,
        browser,
        ai,
        search,
        CompanyCache(tmp_path / "companies.json"),
        storage,
        delivery,
        DeliveryJournal(storage),
        control,
        Clock(),
        events.append,
    )
    return executor, browser, search, delivery, events


def assessment_args(category="clear"):
    return {
        "job_id": "j1",
        "reason": "资料完整，进行评分",
        "assessment": {
            "score": 80,
            "reason": "Python相关",
            "evidence": ["Python"],
            "gaps": [],
            "missing": [],
        },
        "background": {
            "identity_verified": True,
            "employment_checked": True,
            "outsourcing": category,
            "score": 60,
            "reason": "官网依据",
            "source_ids": ["e1"],
        },
    }


async def prepare(executor):
    await executor.execute("inspect_preferences", {"reason": "核对条件"})
    await executor.execute("verify_preferences", {"reason": "核验生效"})
    await executor.execute("list_jobs", {"reason": "读取候选"})


@pytest.mark.parametrize("order", [("read_job", "search_company"), ("search_company", "read_job")])
async def test_different_tool_paths_can_reach_same_assessment(
    config, snapshot, job, storage, tmp_path, order
):
    executor, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(executor)
    for name in order:
        args = {"job_id": "j1", "reason": "收集证据"}
        if name == "search_company":
            args["angle"] = "identity"
        await executor.execute(name, args)
    result = await executor.execute("assess_job", assessment_args())
    assert result["score"] == 76


@pytest.mark.parametrize("enabled,expected", [(True, "excluded"), (False, "pass")])
async def test_suspicion_toggle_is_snapshot_rule(config, snapshot, job, storage, tmp_path, enabled, expected):
    executor, *_ = environment(config, snapshot, job, storage, tmp_path, outsourcing_check=enabled)
    await prepare(executor)
    await executor.execute("read_job", {"job_id": "j1", "reason": "读详情"})
    await executor.execute("search_company", {"job_id": "j1", "reason": "核对业务", "angle": "business"})
    result = await executor.execute("assess_job", assessment_args("suspected"))
    assert result["status"] == expected


async def test_no_evidence_or_unknown_tool_cannot_send(config, snapshot, job, storage, tmp_path):
    executor, browser, search, delivery, _ = environment(
        config, snapshot, job, storage, tmp_path, auto_send=True
    )
    await prepare(executor)
    with pytest.raises(ValueError):
        await executor.execute("send_greeting", {"job_id": "j1", "reason": "直接发"})
    with pytest.raises(ValueError):
        await executor.execute("run_script", {"reason": "绕过"})
    executor.control.pause_requested.set()
    from boss_say_hello.browser.port import ActionBlocked

    with pytest.raises(ActionBlocked):
        await executor.execute("list_jobs", {"reason": "暂停时不能操作"})
    delivery.deliver.assert_not_called()


async def test_agent_client_preserves_tool_message(config):
    from boss_say_hello.ai.agent_client import AgentClient

    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "private thought",
                            "tool_calls": [
                                {
                                    "id": "call1",
                                    "type": "function",
                                    "function": {"name": "finish", "arguments": '{"reason":"done"}'},
                                }
                            ],
                        },
                    }
                ]
            },
        )

    ai = AgentClient(config(), "test-key", httpx.MockTransport(respond))
    try:
        message = await ai.decide([{"role": "user", "content": "test"}], [])
        assert message["tool_calls"][0]["id"] == "call1"
        assert message["reasoning_content"] == "private thought"
        assert bodies[0]["tool_choice"] == "required"
    finally:
        await ai.close()


async def test_loop_returns_real_observations_and_keeps_reasoning_out_of_history(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine

    executor, *_ = environment(config, snapshot, job, storage, tmp_path)

    def decision(name):
        return {
            "role": "assistant",
            "reasoning_content": "PRIVATE_THOUGHT",
            "tool_calls": [
                {
                    "id": name,
                    "type": "function",
                    "function": {"name": name, "arguments": '{"reason":"合成决策"}'},
                }
            ],
        }

    ai = SimpleNamespace(decide=AsyncMock(side_effect=[decision("inspect_preferences"), decision("finish")]))
    await AgentEngine(executor, ai).run()
    second = ai.decide.call_args_list[1].args[0]
    assert any(m.get("tool_call_id") == "inspect_preferences" and "doc" in m["content"] for m in second)
    events = storage.events(executor.run.id)
    assert events[-1].payload["status"] == "agent_finished"
    assert "PRIVATE_THOUGHT" not in str(events)


async def test_new_sources_invalidate_previous_score_and_greeting(config, snapshot, job, storage, tmp_path):
    executor, *_ = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    await prepare(executor)
    await executor.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await executor.execute("search_company", {"job_id": "j1", "reason": "身份", "angle": "identity"})
    await executor.execute("assess_job", assessment_args())
    executor.greetings["j1"] = object()
    await executor.execute("search_company", {"job_id": "j1", "reason": "风险", "angle": "risk"})
    assert "j1" not in executor.assessments and "j1" not in executor.greetings


async def test_loop_budget_ends_without_any_contact(config, snapshot, job, storage, tmp_path):
    from boss_say_hello.agent.engine import AgentEngine

    executor, _, _, delivery, _ = environment(config, snapshot, job, storage, tmp_path, max_agent_steps=2)
    ai = SimpleNamespace(
        decide=AsyncMock(
            return_value={
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "inspect_preferences", "arguments": '{"reason":"检查"}'},
                    }
                ],
            }
        )
    )
    await AgentEngine(executor, ai).run()
    assert storage.events(executor.run.id)[-1].payload["status"] == "budget_reached"
    delivery.deliver.assert_not_called()
