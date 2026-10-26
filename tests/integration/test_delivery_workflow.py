from boss_say_hello.browser.port import ActionResult
from tests.fakes.agent import evaluation_payload


async def test_mixed_jobs_only_eligible_contact(setup_agent, snapshot, agent_config, job):
    jobs = [
        job(id="ok"),
        job(id="low"),
        job(id="pending", salary_raw="面议"),
        job(id="contacted", contact_status="contacted"),
        job(id="model-failure"),
        job(id="unknown"),
    ]
    x = setup_agent(snapshot(config=agent_config(auto_send=True, interval_seconds=0.1)), jobs)

    async def evaluate(run, j, company, sources, **kwargs):
        if j.id == "model-failure":
            raise ValueError("model failure")
        return evaluation_payload(20 if j.id == "low" else 90)

    x.ai.evaluate_job.side_effect = evaluate

    def after(stage):
        if x.browser.calls.count("contact") == 2:
            x.browser.outcomes["message"] = ActionResult("unknown", "送达待核实")

    def emit(e):
        x.events.append(e)
        if e.kind == "state" and e.payload["status"] == "paused":
            x.control.closing.set()

    x.browser.after = after
    x.executor.emit = emit
    await x.engine.run()
    assert x.browser.calls == ["contact", "message", "contact", "message"]
    assert x.journal.contact_count("r1") == 2 and not x.journal.eligible("a1", "unknown")
