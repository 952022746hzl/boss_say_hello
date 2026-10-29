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
        release_run=lambda: None,
        release_candidates=lambda job_ids: None,
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
    from boss_say_hello.domain import Greeting

    ai = SimpleNamespace(greet=AsyncMock(return_value=Greeting("您好，我有相关开发经验，希望进一步沟通。")))
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


def evaluation_payload(category="clear"):
    args = assessment_args(category)
    return {k: args[k] for k in ("assessment", "background")}


def evaluator(category="clear"):
    return SimpleNamespace(evaluate_job=AsyncMock(return_value=evaluation_payload(category)))


@pytest.mark.parametrize("order", [("read_job", "search_company"), ("search_company", "read_job")])
async def test_different_tool_paths_can_reach_same_assessment(
    config, snapshot, job, storage, tmp_path, order
):
    executor, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(executor)
    for name in order:
        args = {"job_id": "j1", "reason": "收集证据"}
        await executor.execute(name, args)
    result = await executor.execute("assess_job", assessment_args())
    assert result["score"] == 76


@pytest.mark.parametrize("enabled,expected", [(True, "excluded"), (False, "pass")])
async def test_suspicion_toggle_is_snapshot_rule(config, snapshot, job, storage, tmp_path, enabled, expected):
    executor, *_ = environment(config, snapshot, job, storage, tmp_path, outsourcing_check=enabled)
    await prepare(executor)
    await executor.execute("read_job", {"job_id": "j1", "reason": "读详情"})
    await executor.execute("search_company", {"job_id": "j1", "reason": "核对业务"})
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


async def test_agent_client_evaluates_current_job_once_without_action_tools(config, snapshot, job):
    from boss_say_hello.ai.agent_client import AgentClient

    bodies = []
    payload = evaluation_payload()

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                    }
                ]
            },
        )

    ai = AgentClient(config(), "test-key", httpx.MockTransport(respond))
    try:
        run = snapshot()
        sources = [{"id": "e1", "url": "https://example.com", "text": "source-text"}]
        assert await ai.evaluate_job(run, job(), {"name": "Example"}, sources) == payload
        assert len(bodies) == 1
        assert "tools" not in bodies[0] and "tool_choice" not in bodies[0]
        assert bodies[0]["response_format"] == {"type": "json_object"}
        context = json.loads(bodies[0]["messages"][1]["content"])
        assert context["当前岗位"]["id"] == "j1"
        assert context["公司资料"] == sources and context["source_ids"] == ["e1"]
        assert run.config.prompts.greeting not in str(context)
    finally:
        await ai.close()


async def test_loop_passes_current_observations_without_model_action_history(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.agent.engine import AgentEngine

    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    ai = evaluator()
    await AgentEngine(ex, ai).run()
    run, current, company, sources = ai.evaluate_job.await_args.args
    assert run is ex.run and current.id == "j1" and company["name"] == "Example"
    assert sources[0]["id"] == "e1"
    events = storage.events(ex.run.id)
    assert events[-1].payload["status"] == "completed"
    actions = [e.payload["tool"] for e in events if e.kind == "tool_action"]
    assert actions == [
        "inspect_preferences",
        "verify_preferences",
        "list_jobs",
        "read_job",
        "search_company",
        "assess_job",
        "draft_greeting",
    ]


async def test_duplicate_company_query_preserves_same_evidence_score_and_greeting(
    config, snapshot, job, storage, tmp_path
):
    executor, *_ = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    await prepare(executor)
    await executor.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await executor.execute("search_company", {"job_id": "j1", "reason": "身份"})
    await executor.execute("assess_job", assessment_args())
    executor.greetings["j1"] = object()
    await executor.execute("search_company", {"job_id": "j1", "reason": "风险"})
    assert "j1" in executor.assessments and "j1" in executor.greetings
    assert executor.search.search.await_count == 1


async def test_exhausted_model_budget_skips_job_without_any_contact(config, snapshot, job, storage, tmp_path):
    from boss_say_hello.agent.engine import AgentEngine

    ex, _, _, delivery, _ = environment(config, snapshot, job, storage, tmp_path)
    ex.job_steps["j1"] = 12
    ai = evaluator()
    await AgentEngine(ex, ai).run()
    assert storage.events(ex.run.id)[-1].payload["status"] == "completed"
    ai.evaluate_job.assert_not_called()
    assert "j1" in ex.done
    delivery.deliver.assert_not_called()


async def test_default_greeting_warning_allows_visible_resume(config, snapshot, job, storage, tmp_path):
    import asyncio

    from boss_say_hello.agent.engine import AgentEngine

    ex, *_ = environment(config, snapshot, job, storage, tmp_path)
    ex.control.default_message_required = Event()
    ex.control.default_message_acknowledged = Event()
    ex.control.default_message_required.set()
    ex.control.pause_requested.set()
    task = asyncio.create_task(AgentEngine(ex, SimpleNamespace()).run())
    try:
        await asyncio.sleep(0.02)
        assert any(e.kind == "state" and e.payload.get("requires_default_ack") for e in storage.events("r1"))
    finally:
        ex.control.closing.set()
        await task
