from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from boss_say_hello.domain import PromptSet
from boss_say_hello.resume.service import ResumeService
from tests.test_resume import make_pdf


async def test_pdf_to_confirmed_context_and_preview(
    tmp_path, storage, snapshot, agent_config, setup_agent
):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    extractor = SimpleNamespace(extract_resume=AsyncMock(return_value=(("Python",), "原始摘要")))
    service = ResumeService(storage, extractor, tmp_path / "managed")
    draft = await service.import_pdf(path)
    assert not draft.confirmed
    confirmed = service.confirm(service.revise(draft, ("Python", "SQL"), "用户核对后的摘要"))
    run = snapshot(resume=confirmed, config=agent_config())
    run = replace(run, config=replace(run.config, prompts=PromptSet("偏好", "评分", "风格")))
    x = setup_agent(run)
    await x.engine.run()
    assert x.browser.calls == []
    assert x.ai.evaluate_job.call_args.args[0].resume.summary == "用户核对后的摘要"
    assert storage.list_runs()[0].config.prompts == run.config.prompts
    assert any(e.kind == "greeting" for e in storage.events(run.id))
    assert x.events[-1].payload["status"] == "completed"
