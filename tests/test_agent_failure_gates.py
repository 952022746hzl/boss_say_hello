from unittest.mock import AsyncMock

import pytest

from boss_say_hello.ai.schemas import AiFailure
from tests.test_agent_engine import assessment_args, environment, prepare


async def ready_to_send(config, snapshot, job, storage, tmp_path):
    ex, *rest = environment(config, snapshot, job, storage, tmp_path, auto_send=True)
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    await ex.execute("search_company", {"job_id": "j1", "reason": "身份", "angle": "identity"})
    await ex.execute("assess_job", assessment_args())
    ex.greetings["j1"] = object()
    return ex, *rest


async def test_failed_refresh_of_company_evidence_revokes_eligibility(
    config, snapshot, job, storage, tmp_path
):
    ex, _, search, delivery, _ = await ready_to_send(config, snapshot, job, storage, tmp_path)
    search.search = AsyncMock(side_effect=AiFailure("search_failed"))
    with pytest.raises(AiFailure):
        await ex.execute("search_company", {"job_id": "j1", "reason": "补充风险", "angle": "risk"})
    with pytest.raises(ValueError):
        await ex.execute("send_greeting", {"job_id": "j1", "reason": "旧分不能发送"})
    delivery.deliver.assert_not_called()


async def test_nested_undeclared_tool_fields_rejected(config, snapshot, job, storage, tmp_path):
    ex, *_ = await ready_to_send(config, snapshot, job, storage, tmp_path)
    args = assessment_args()
    args["background"]["bypass"] = True
    with pytest.raises(ValueError):
        await ex.execute("assess_job", args)


async def test_manual_change_to_unconfigured_filter_revokes_receipt(config):
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    browser = NodriverAdapter()
    browser.bind_goal(config(target_title="Java", cities=("深圳",), salary_min=20000, salary_max=30000))
    original = {
        "document": "doc",
        "path": "/web/geek/job-recommend",
        "city": "深圳",
        "expectations": [{"label": "Java(深圳)", "selected": True}],
        "filters": [{"field": "薪资", "selected": ["20-50K"]}, {"field": "学历", "selected": ["不限"]}],
    }
    current = {**original, "filters": [original["filters"][0], {"field": "学历", "selected": ["本科"]}]}
    browser.preference_snapshot = AsyncMock(return_value=current)
    assert not await browser.verify_preferences(original)


def test_stop_run_does_not_close_application(qtbot):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread

    worker = WorkerThread()
    c = Controller(worker)
    c.request_stop()
    assert c.control.closing.is_set()
    assert c.control.pause_requested.is_set()


async def test_delivery_rejects_changed_company_legal_identity(snapshot, config, job, storage):
    from boss_say_hello.delivery.journal import DeliveryJournal
    from boss_say_hello.delivery.service import DeliveryService
    from boss_say_hello.domain import Assessment, Greeting
    from boss_say_hello.runtime.control import RunControl
    from tests.fakes.browser import FakeBrowser

    old = job(company_id="old-legal")
    browser = FakeBrowser([old])
    browser.refresh_job = AsyncMock(return_value=job(company_id="new-legal"))
    run = snapshot(config=config(auto_send=True))
    events = await DeliveryService(browser, DeliveryJournal(storage)).deliver(
        run, old, Assessment(90, "ok", (), (), ()), Greeting("您好"), RunControl()
    )
    assert events[0].payload["status"] == "changed"
