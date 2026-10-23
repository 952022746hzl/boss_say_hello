from uuid import uuid4

from boss_say_hello.browser.port import ActionResult
from boss_say_hello.diagnostics import record


class DeliveryJournal:
    def __init__(self, store):
        self.store = store
        self.db = store.db

    def eligible(self, account_id, job_id):
        return not self.db.execute(
            "SELECT 1 FROM contacts WHERE account_id=? AND job_id=? AND cancelled=0", (account_id, job_id)
        ).fetchone()

    def contact_count(self, run_id):
        return self.db.execute(
            "SELECT COUNT(*) FROM contacts WHERE run_id=? AND cancelled=0", (run_id,)
        ).fetchone()[0]

    def reserve(self, run, job):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if (
                not self.eligible(run.account_id, job.id)
                or self.contact_count(run.id) >= run.config.max_contacts
            ):
                return None
            id = uuid4().hex
            self.db.execute(
                "INSERT INTO contacts(id,run_id,account_id,job_id) VALUES (?,?,?,?)",
                (id, run.id, run.account_id, job.id),
            )
            record("delivery.reserved", run_id=run.id, job_id=job.id, attempt_id=id)
            return id

    def begin_stage(self, attempt_id, stage):
        with self.db:
            self.db.execute(
                "INSERT INTO delivery_attempts VALUES (?,?,?,?)", (attempt_id, stage, "intent", "")
            )
        record("delivery.intent", attempt_id=attempt_id, stage=stage, status="intent")

    def finish_stage(self, attempt_id, stage, result: ActionResult):
        with self.db:
            self.db.execute(
                "UPDATE delivery_attempts SET outcome=?,detail=? WHERE contact_id=? AND stage=?",
                (result.outcome, result.detail, attempt_id, stage),
            )
        record(
            "delivery.result",
            attempt_id=attempt_id,
            stage=stage,
            status=result.outcome,
            level="WARNING" if result.outcome in ("unknown", "failed") else "INFO",
        )

    def cancel_before_contact(self, attempt_id):
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM delivery_attempts WHERE contact_id=? AND outcome!='cancelled'", (attempt_id,)
            ).fetchone():
                raise ValueError("不能取消已经尝试发送的记录")
            self.db.execute("UPDATE contacts SET cancelled=1 WHERE id=?", (attempt_id,))

    def recover_incomplete(self):
        with self.db:
            self.db.execute(
                "UPDATE delivery_attempts SET outcome='unknown',detail='上次运行中断，结果待核实' WHERE outcome='intent'"
            )
            self.db.execute(
                "INSERT INTO delivery_attempts SELECT id,'contact','unknown','中断前已预留，无法核实是否联系' FROM contacts WHERE cancelled=0 AND NOT EXISTS(SELECT 1 FROM delivery_attempts WHERE contact_id=contacts.id)"
            )

    def stages(self, attempt_id):
        return {
            row["stage"]: {"outcome": row["outcome"], "detail": row["detail"]}
            for row in self.db.execute("SELECT * FROM delivery_attempts WHERE contact_id=?", (attempt_id,))
        }
