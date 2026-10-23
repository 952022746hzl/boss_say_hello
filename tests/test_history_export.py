import json

from boss_say_hello.browser.port import ActionResult
from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.domain import RunEvent, public_dict


def test_job_json_separates_drafts_sent_and_unknown_and_survives_rebuild(tmp_path, storage, snapshot, job):
    journal = DeliveryJournal(storage)
    for index, outcome in enumerate(("preview", "succeeded", "unknown")):
        run = snapshot(id=f"run-{index}")
        current = job(id=f"job-{index}")
        storage.save_run(run)
        storage.append_event(RunEvent(run.id, "job", {"job_id": current.id, "job": public_dict(current)}))
        storage.append_event(
            RunEvent(run.id, "greeting", {"job_id": current.id, "text": "synthetic greeting"})
        )
        if outcome != "preview":
            attempt = journal.reserve(run, current)
            journal.begin_stage(attempt, "contact")
            journal.finish_stage(attempt, "contact", ActionResult("succeeded", "verified"))
            journal.begin_stage(attempt, "message")
            journal.finish_stage(attempt, "message", ActionResult(outcome, "verified"))
    # Seeing the same job again must keep its first run and draft without declaring it sent.
    run = snapshot(id="run-revisit")
    storage.save_run(run)
    storage.append_event(RunEvent(run.id, "job", {"job_id": "job-0", "job": public_dict(job(id="job-0"))}))
    path = tmp_path / "history" / "jobs.json"
    storage.export_history(path)
    rows = {row["job_id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["jobs"]}
    assert len(rows) == 3
    assert len(rows["job-0"]["encounters"]) == 2
    assert not rows["job-0"]["message_sent"] and not rows["job-0"]["skip_auto_contact"]
    assert rows["job-1"]["message_sent"] and rows["job-1"]["skip_auto_contact"]
    assert rows["job-2"]["result_unknown"] and not rows["job-2"]["message_sent"]
    assert rows["job-2"]["skip_auto_contact"]
    expected = path.read_text(encoding="utf-8")
    path.unlink()
    storage.export_history(path)
    assert path.read_text(encoding="utf-8") == expected


def test_job_json_isolates_accounts(storage, snapshot, job, tmp_path):
    for index in (1, 2):
        run = snapshot(id=f"run-{index}", account_id=f"a{index}")
        storage.save_run(run)
        storage.append_event(RunEvent(run.id, "job", {"job_id": "same", "job": public_dict(job(id="same"))}))
    path = tmp_path / "jobs.json"
    storage.export_history(path)
    rows = json.loads(path.read_text(encoding="utf-8"))["jobs"]
    assert len(rows) == 2
    assert {row["account_id"] for row in rows} == {"a1", "a2"}
