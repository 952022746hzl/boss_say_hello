import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from boss_say_hello.domain import ResumeVersion, RunEvent, RunSnapshot, resume_from_dict, snapshot_from_dict
from boss_say_hello.storage.json_file import write_json


class RecordStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS resume_versions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS run_events(seq INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, account_id TEXT NOT NULL,
                job_id TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS active_contact ON contacts(account_id,job_id) WHERE cancelled=0;
            CREATE TABLE IF NOT EXISTS delivery_attempts(contact_id TEXT, stage TEXT, outcome TEXT, detail TEXT,
                PRIMARY KEY(contact_id,stage));
            PRAGMA user_version=1;
        """)

    def save_resume(self, resume: ResumeVersion) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO resume_versions VALUES (?,?)",
                (resume.id, json.dumps(asdict(resume), ensure_ascii=False)),
            )

    def get_resume(self, id: str) -> ResumeVersion:
        row = self.db.execute("SELECT data FROM resume_versions WHERE id=?", (id,)).fetchone()
        if row is None:
            raise KeyError(id)
        return resume_from_dict(json.loads(row["data"]))

    def latest_resume(self) -> ResumeVersion | None:
        row = self.db.execute("SELECT data FROM resume_versions ORDER BY rowid DESC LIMIT 1").fetchone()
        return resume_from_dict(json.loads(row["data"])) if row else None

    def save_run(self, run: RunSnapshot) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES (?,?)", (run.id, json.dumps(asdict(run), ensure_ascii=False))
            )

    def append_event(self, event: RunEvent) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO run_events(run_id,kind,data) VALUES (?,?,?)",
                (event.run_id, event.kind, json.dumps(event.payload, ensure_ascii=False)),
            )

    def list_runs(self) -> list[RunSnapshot]:
        return [
            snapshot_from_dict(json.loads(row[0]))
            for row in self.db.execute("SELECT data FROM runs ORDER BY rowid DESC")
        ]

    def events(self, run_id: str) -> list[RunEvent]:
        events = [
            RunEvent(run_id, row["kind"], json.loads(row["data"]))
            for row in self.db.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY seq", (run_id,))
        ]
        # The journal is authoritative even if the process stopped before the
        # corresponding UI/event notification was persisted.
        expected = ["contact", "message"]
        for contact in self.db.execute(
            "SELECT id,job_id FROM contacts WHERE run_id=? AND cancelled=0", (run_id,)
        ):
            stages = {
                row["stage"]: {"outcome": row["outcome"], "detail": row["detail"]}
                for row in self.db.execute(
                    "SELECT * FROM delivery_attempts WHERE contact_id=?", (contact["id"],)
                )
            }
            for stage in expected:
                stages.setdefault(stage, {"outcome": "not_started", "detail": "未执行"})
            outcomes = {v["outcome"] for v in stages.values()}
            status = (
                "unknown"
                if outcomes & {"unknown", "intent"}
                else "failed"
                if "failed" in outcomes
                else "sent"
                if outcomes == {"succeeded"}
                else "interrupted"
            )
            if not any(
                e.kind == "delivery"
                and e.payload.get("job_id") == contact["job_id"]
                and e.payload.get("stages") == stages
                for e in events
            ):
                events.append(
                    RunEvent(
                        run_id,
                        "delivery",
                        {
                            "job_id": contact["job_id"],
                            "status": status,
                            "stages": stages,
                            "reason": "持久化发送记录（包含中断前已完成的步骤）",
                            "occupies_slot": True,
                        },
                    )
                )
        return events

    def export_history(self, path: Path) -> None:
        jobs = {}
        runs = []
        for run in reversed(self.list_runs()):
            metadata = {
                "run_id": run.id,
                "account_id": run.account_id,
                "created_at": run.created_at,
                "mode": "auto" if run.config.auto_send else "preview",
            }
            runs.append(metadata)
            encounters = {}
            for event in self.events(run.id):
                payload = event.payload
                if event.kind in ("tool_action", "tool_result", "preferences"):
                    metadata.setdefault("steps", []).append({"kind": event.kind, **payload})
                if event.kind == "finished":
                    metadata["status"] = payload["status"]
                job_id = payload.get("job_id")
                if not job_id:
                    continue
                key = (run.account_id, job_id)
                if key not in jobs:
                    jobs[key] = {
                        "account_id": run.account_id,
                        "job_id": job_id,
                        "browsed": False,
                        "message_sent": False,
                        "result_unknown": False,
                        "skip_auto_contact": False,
                        "encounters": [],
                    }
                row = jobs[key]
                if job_id not in encounters:
                    encounters[job_id] = {
                        "run_id": run.id,
                        "created_at": run.created_at,
                        "mode": metadata["mode"],
                    }
                    row["encounters"].append(encounters[job_id])
                encounter = encounters[job_id]
                if event.kind == "job":
                    row["browsed"] = True
                    row["job"] = payload["job"]
                    row["skip_auto_contact"] |= payload["job"].get("contact_status") == "contacted"
                elif event.kind in ("tool_action", "tool_result"):
                    encounter.setdefault("steps", []).append({"kind": event.kind, **payload})
                elif event.kind in (
                    "assessment",
                    "check",
                    "greeting",
                    "delivery",
                    "error",
                    "background",
                    "company_sources",
                ):
                    encounter[event.kind] = {k: v for k, v in payload.items() if k != "job_id"}
        # Confirmed sends and deduplication are derived from the durable journal, never from drafts.
        for contact in self.db.execute("SELECT * FROM contacts WHERE cancelled=0"):
            row = jobs.get((contact["account_id"], contact["job_id"]))
            if row is None:
                continue
            row["skip_auto_contact"] = True
            for stage in self.db.execute(
                "SELECT stage,outcome FROM delivery_attempts WHERE contact_id=?", (contact["id"],)
            ):
                row["message_sent"] |= stage["stage"] == "message" and stage["outcome"] == "succeeded"
                row["result_unknown"] |= stage["outcome"] in ("intent", "unknown")
        write_json(path, {"schema_version": 1, "jobs": list(jobs.values()), "runs": runs})

    def close(self) -> None:
        self.db.close()
