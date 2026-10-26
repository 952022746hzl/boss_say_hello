from types import SimpleNamespace
from unittest.mock import AsyncMock

from boss_say_hello.agent.engine import AgentEngine
from boss_say_hello.agent.tools import ToolExecutor
from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.delivery.service import DeliveryService
from boss_say_hello.domain import Greeting
from boss_say_hello.runtime.control import RunControl
from boss_say_hello.storage.companies import CompanyCache
from tests.fakes.browser import FakeBrowser
from tests.fakes.clock import FakeClock


def evaluation_payload(score=90):
    return {
        "assessment": {
            "score": score,
            "reason": "具备相关 Python 项目经验",
            "evidence": ["Python"],
            "gaps": [],
            "missing": [],
        },
        "background": {
            "identity_verified": True,
            "employment_checked": True,
            "outsourcing": "clear",
            "score": 90,
            "reason": "官网确认主体与直接招聘",
            "source_ids": ["e1"],
        },
    }


def make_agent(run, jobs, storage, tmp_path):
    browser = FakeBrowser(jobs)
    ai = SimpleNamespace(
        evaluate_job=AsyncMock(return_value=evaluation_payload()),
        greet=AsyncMock(return_value=Greeting("你好，我有 Python 开发经验。")),
    )
    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "id": "e1",
                    "url": "https://company.example/about",
                    "title": "公司官网",
                    "text": "公司自主研发产品，直接招聘研发人员",
                }
            ]
        )
    )
    journal, control, clock, events = DeliveryJournal(storage), RunControl(), FakeClock(), []
    executor = ToolExecutor(
        run,
        browser,
        ai,
        search,
        CompanyCache(tmp_path / "companies.json"),
        storage,
        DeliveryService(browser, journal),
        journal,
        control,
        clock,
        events.append,
    )
    return SimpleNamespace(
        run=run,
        engine=AgentEngine(executor, ai),
        executor=executor,
        browser=browser,
        ai=ai,
        journal=journal,
        events=events,
        clock=clock,
        control=control,
    )
