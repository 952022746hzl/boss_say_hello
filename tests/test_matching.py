from boss_say_hello.ai.schemas import AiFailure


async def test_rules_reject_before_ai(setup_agent, snapshot, agent_config):
    x = setup_agent(snapshot(config=agent_config(cities=("上海",))))
    await x.engine.run()
    x.ai.evaluate_job.assert_not_called()
    assert not x.browser.calls
    assert any(e.kind == "check" and e.payload["status"] == "reject" for e in x.events)


async def test_no_keyword_count_gate(setup_agent, job):
    x = setup_agent(jobs=[job(text="A relevant role without exact keywords")])
    await x.engine.run()
    assert any(e.kind == "assessment" and e.payload["score"] == 90 for e in x.events)
    assert any(e.kind == "greeting" for e in x.events)


async def test_failed_score_is_pending_without_greeting_or_delivery(setup_agent):
    x = setup_agent()
    x.ai.evaluate_job.side_effect = AiFailure("invalid_fields")
    await x.engine.run()
    assert x.ai.evaluate_job.await_count == 2
    assert not any(e.kind in {"assessment", "greeting"} for e in x.events)
    assert any(e.kind == "check" and e.payload["status"] == "pending" for e in x.events)
    assert not x.browser.calls
