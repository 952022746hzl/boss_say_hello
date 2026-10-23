from types import SimpleNamespace
from unittest.mock import AsyncMock

from boss_say_hello.domain import Greeting
from tests.test_agent_engine import assessment_args, environment


async def test_backend_runs_agent_to_preview(config, snapshot, job, storage, tmp_path, resume, monkeypatch):
    import json

    from boss_say_hello.runtime.backend import Backend

    ex, browser, search, _, _ = environment(config, snapshot, job, storage, tmp_path)
    sequence = [
        ("inspect_preferences", {}),
        ("verify_preferences", {}),
        ("list_jobs", {}),
        ("read_job", {"job_id": "j1"}),
        ("search_company", {"job_id": "j1", "angle": "employment"}),
        ("assess_job", assessment_args()),
        ("draft_greeting", {"job_id": "j1"}),
        ("finish", {}),
    ]
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": str(i),
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"reason": "合成验收", **args})},
                }
            ],
        }
        for i, (name, args) in enumerate(sequence)
    ]
    ai = SimpleNamespace(
        decide=AsyncMock(side_effect=messages),
        greet=AsyncMock(return_value=Greeting("您好，我有 Python 项目经验", ("Python",))),
        close=AsyncMock(),
    )
    monkeypatch.setattr("boss_say_hello.runtime.backend.AgentClient", lambda *args: ai)
    search.close = AsyncMock()
    monkeypatch.setattr("boss_say_hello.runtime.backend.CompanySearch", lambda *args: search)
    browser.close = AsyncMock()
    events = []
    backend = Backend(events.append, root=tmp_path / "backend", browser=browser)
    await backend.initialize()
    backend.secret = AsyncMock(return_value="fake")
    try:
        await backend.run(ex.run.config, resume(), ex.control)
        assert any(e.kind == "greeting" for e in events)
        assert any(e.kind == "finished" and e.payload["status"] == "agent_finished" for e in events)
        assert not backend.journal.contact_count(events[-1].run_id)
    finally:
        await backend.close()
