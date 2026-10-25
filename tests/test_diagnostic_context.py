import asyncio

import pytest

from boss_say_hello import diagnostics


@pytest.mark.asyncio
async def test_context_correlates_nested_operations_without_leaking_between_tasks(tmp_path):
    entries = []
    log = diagnostics.EventLog(tmp_path, entries.append)

    async def task(run):
        with diagnostics.scope(run_id=run, step=3):
            await asyncio.sleep(0)
            with diagnostics.scope(tool="preference_action"):
                diagnostics.record(
                    "browser.preference.snapshot", level="DEBUG", control_id="tag", secret="no"
                )
        diagnostics.record("outside")

    try:
        with log.bind():
            await asyncio.gather(task("r1"), task("r2"))
        nested = [e["fields"] for e in entries if e["level"] == "DEBUG"]
        assert {e["run_id"] for e in nested} == {"r1", "r2"}
        assert all(
            e["step"] == 3 and e["tool"] == "preference_action" and e["control_id"] == "tag" for e in nested
        )
        assert all("secret" not in e for e in nested)
        assert all(not e["fields"] for e in entries if e["event"] == "outside")
    finally:
        log.close()
