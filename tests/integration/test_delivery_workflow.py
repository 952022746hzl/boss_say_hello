from boss_say_hello.browser.port import ActionResult
from boss_say_hello.domain import Assessment


async def test_mixed_jobs_only_eligible_contact(setup_engine, snapshot, config, job):
    jobs = [
        job(id="ok"),
        job(id="low"),
        job(id="pending", salary_raw="面议"),
        job(id="contacted", contact_status="contacted"),
        job(id="model-failure"),
        job(id="unknown"),
    ]
    x = setup_engine(jobs)

    async def assess(run, j):
        if j.id == "model-failure":
            raise ValueError("model failure")
        return Assessment(20 if j.id == "low" else 90, "测试评分", ("Python",), (), ())

    x.ai.assess.side_effect = assess

    def after(stage):
        if x.browser.calls.count("contact") == 2:
            x.browser.outcomes["message"] = ActionResult("unknown", "送达待核实")

    def emit(e):
        x.events.append(e)
        if e.kind == "state" and e.payload["status"] == "paused":
            x.control.closing.set()

    x.browser.after = after
    x.engine.emit = emit
    await x.engine.run(snapshot(config=config(auto_send=True, interval_seconds=0.1)), x.control)
    assert x.browser.calls == ["contact", "message", "contact", "message"]
    assert x.journal.contact_count("r1") == 2 and not x.journal.eligible("a1", "unknown")
