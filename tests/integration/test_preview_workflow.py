from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.delivery.service import DeliveryService
from boss_say_hello.domain import Assessment, Greeting, PromptSet
from boss_say_hello.greetings.service import GreetingService
from boss_say_hello.matching.service import MatchingService
from boss_say_hello.resume.service import ResumeService
from boss_say_hello.runs.control import Clock
from boss_say_hello.runs.engine import RunEngine
from boss_say_hello.runtime.control import RunControl
from tests.fakes.browser import FakeBrowser
from tests.test_resume import make_pdf


async def test_pdf_to_confirmed_context_and_preview(tmp_path, storage, snapshot, job):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    ai = SimpleNamespace(
        extract_resume=AsyncMock(return_value=(("Python",), "原始摘要")),
        assess=AsyncMock(return_value=Assessment(85, "匹配", ("Python",), (), ())),
        greet=AsyncMock(return_value=Greeting("你好，有 Python 项目经验。")),
    )
    service = ResumeService(storage, ai, tmp_path / "managed")
    draft = await service.import_pdf(path)
    assert not draft.confirmed
    confirmed = service.confirm(service.revise(draft, ("Python", "SQL"), "用户核对后的摘要"))
    run = snapshot(resume=confirmed)
    run = replace(run, config=replace(run.config, prompts=PromptSet("偏好", "评分", "风格")))
    browser = FakeBrowser([job()])
    journal = DeliveryJournal(storage)
    await RunEngine(
        storage,
        browser,
        MatchingService(ai),
        GreetingService(ai),
        DeliveryService(browser, journal),
        journal,
        Clock(),
        lambda e: None,
    ).run(run, RunControl())
    assert browser.calls == []
    assert ai.assess.call_args.args[0].resume.summary == "用户核对后的摘要"
    assert storage.list_runs()[0].config.prompts == run.config.prompts
    assert any(e.kind == "greeting" for e in storage.events(run.id))
