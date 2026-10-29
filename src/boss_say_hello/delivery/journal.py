from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from boss_say_hello.browser.port import ActionResult
from boss_say_hello.delivery.company_quota import (
    beijing_day_bounds,
    company_full_name,
    company_identity,
    utc_timestamp,
)
from boss_say_hello.diagnostics import record


class DeliveryJournal:
    def __init__(self, store, *, now: Callable[[], datetime] | None = None):
        self.store = store
        self.db = store.db
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_reservation_reason = None

    def eligible(self, account_id, job_id):
        return not self.db.execute(
            "SELECT 1 FROM contacts WHERE account_id=? AND job_id=? AND cancelled=0", (account_id, job_id)
        ).fetchone()

    def contact_count(self, run_id):
        return self.db.execute(
            "SELECT COUNT(*) FROM contacts WHERE run_id=? AND cancelled=0", (run_id,)
        ).fetchone()[0]

    def success_count(self, run_id):
        # Count only this run's verified text deliveries; attempts remain for deduplication.
        return self.db.execute(
            """SELECT COUNT(*) FROM contacts c
               WHERE c.run_id=? AND c.cancelled=0
                 AND EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.contact_id=c.id
                             AND d.stage='contact' AND d.outcome='succeeded')
                 AND EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.contact_id=c.id
                             AND d.stage='message' AND d.outcome='succeeded')""",
            (run_id,),
        ).fetchone()[0]

    def company_limit_reason(self, run, job) -> str | None:
        if not run.config.auto_send:
            return None
        # A verified ID/name observation is useful even when this candidate is
        # blocked. Own the transaction here, but never commit reserve's lock.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._company_limit_reason(run, job)

    def _company_limit_reason(self, run, job) -> str | None:
        if not run.config.auto_send:
            return None
        identity = company_identity(job)
        if identity is None:
            return "公司身份待确认：缺少可靠的平台公司 ID、公司链接或完整公司名称，本次不自动发送"
        name = company_full_name(job)
        if identity.startswith("id:") and name:
            self.db.execute(
                "INSERT OR IGNORE INTO company_identity_aliases VALUES (?,?,?)",
                (run.account_id, name, identity),
            )
        if identity.startswith("name:"):
            ids = self.db.execute(
                "SELECT company_key FROM company_identity_aliases WHERE account_id=? AND company_name=?",
                (run.account_id, name),
            ).fetchall()
            if len(ids) > 1:
                return "公司身份待确认：完整公司名称对应多个平台公司 ID，本次不自动发送"
            if ids:
                identity = ids[0]["company_key"]

        keys, ambiguous_keys = {identity}, set()
        if identity.startswith("id:"):
            aliases = self.db.execute(
                """SELECT company_name,COUNT(*) AS id_count FROM company_identity_aliases
                   WHERE account_id=? AND company_name IN (
                       SELECT company_name FROM company_identity_aliases WHERE account_id=? AND company_key=?)
                   GROUP BY company_name""",
                (run.account_id, run.account_id, identity),
            )
            for alias in aliases:
                target = keys if alias["id_count"] == 1 else ambiguous_keys
                target.add(f"name:{alias['company_name']}")
        start, end = beijing_day_bounds(self.now())
        if ambiguous_keys:
            uncertain = self._company_usage(run.account_id, ambiguous_keys, start, end)
            if uncertain["successes"] or uncertain["pending"]:
                return (
                    "公司身份待确认：历史完整名称记录对应多个平台公司 ID，"
                    "今日成功或处理中预留的归属尚不明确，本次不自动发送"
                )
        row = self._company_usage(run.account_id, keys, start, end)
        limit = run.config.max_company_daily_contacts
        if row["successes"] + row["pending"] >= limit:
            return (
                f"同公司今日已成功联系 {row['successes']} 个岗位，处理中预留 {row['pending']} 个，"
                f"已达每日上限 {limit} 个（北京时间）"
            )
        return None

    def _company_usage(self, account_id, keys, start, end):
        placeholders = ",".join("?" for _ in keys)
        return self.db.execute(
            f"""SELECT COALESCE(SUM(CASE WHEN m.timestamp_status='verified'
                          AND m.succeeded_at>=? AND m.succeeded_at<?
                          AND EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.contact_id=c.id
                                      AND d.stage='contact' AND d.outcome='succeeded')
                          AND EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.contact_id=c.id
                                      AND d.stage='message' AND d.outcome='succeeded')
                          THEN 1 ELSE 0 END),0) AS successes,
                      COALESCE(SUM(m.quota_reserved),0) AS pending
               FROM contacts c JOIN contact_company_metadata m ON m.contact_id=c.id
               WHERE c.account_id=? AND c.cancelled=0
                 AND m.company_key IN ({placeholders})""",
            (start, end, account_id, *keys),
        ).fetchone()

    def company_quota_history_notice(self) -> str | None:
        if self.db.execute(
            """SELECT 1 FROM contacts c JOIN contact_company_metadata m ON m.contact_id=c.id
               WHERE c.cancelled=0 AND m.timestamp_status='legacy_unknown'
                 AND EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.contact_id=c.id
                             AND d.stage='contact' AND d.outcome='succeeded')
                 AND EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.contact_id=c.id
                             AND d.stage='message' AND d.outcome='succeeded') LIMIT 1"""
        ).fetchone():
            return (
                "旧版历史记录缺少可核验的逐条成功时间，不计入当天公司配额；"
                "原岗位去重仍保留，公司每日配额从升级后可核验的新记录开始统计"
            )
        return None

    def reserve(self, run, job):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.last_reservation_reason = None
            if not self.eligible(run.account_id, job.id):
                self.last_reservation_reason = "已联系或结果待核实，不重复发送同一岗位"
                return None
            if self.success_count(run.id) >= run.config.max_contacts:
                self.last_reservation_reason = "已达本轮成功发送目标"
                return None
            self.last_reservation_reason = self._company_limit_reason(run, job)
            if self.last_reservation_reason:
                return None
            id = uuid4().hex
            self.db.execute(
                "INSERT INTO contacts(id,run_id,account_id,job_id) VALUES (?,?,?,?)",
                (id, run.id, run.account_id, job.id),
            )
            self.db.execute(
                """INSERT INTO contact_company_metadata
                   (contact_id,company_key,company_name,reserved_at,quota_reserved,timestamp_status)
                   VALUES (?,?,?,?,?,'pending')""",
                (
                    id,
                    company_identity(job),
                    company_full_name(job),
                    utc_timestamp(self.now()),
                    int(run.config.auto_send),
                ),
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
            stages = self.stages(attempt_id)
            if all(stages.get(name, {}).get("outcome") == "succeeded" for name in ("contact", "message")):
                self.db.execute(
                    """UPDATE contact_company_metadata SET succeeded_at=?,timestamp_status='verified'
                       WHERE contact_id=? AND succeeded_at IS NULL AND timestamp_status='pending'""",
                    (utc_timestamp(self.now()), attempt_id),
                )
            if result.outcome != "succeeded" or stage == "message":
                self.db.execute(
                    "UPDATE contact_company_metadata SET quota_reserved=0 WHERE contact_id=?", (attempt_id,)
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
            self.db.execute(
                "UPDATE contact_company_metadata SET quota_reserved=0 WHERE contact_id=?", (attempt_id,)
            )

    def release_company_reservation(self, attempt_id):
        """Release processing capacity after stopping; keep permanent job deduplication."""
        with self.db:
            self.db.execute(
                "UPDATE contact_company_metadata SET quota_reserved=0 WHERE contact_id=?", (attempt_id,)
            )

    def recover_incomplete(self):
        with self.db:
            self.db.execute(
                "UPDATE delivery_attempts SET outcome='unknown',detail='上次运行中断，结果待核实' WHERE outcome='intent'"
            )
            self.db.execute(
                "INSERT INTO delivery_attempts SELECT id,'contact','unknown','中断前已预留，无法核实是否联系' FROM contacts WHERE cancelled=0 AND NOT EXISTS(SELECT 1 FROM delivery_attempts WHERE contact_id=contacts.id)"
            )
            self.db.execute("UPDATE contact_company_metadata SET quota_reserved=0 WHERE quota_reserved=1")

    def stages(self, attempt_id):
        return {
            row["stage"]: {"outcome": row["outcome"], "detail": row["detail"]}
            for row in self.db.execute("SELECT * FROM delivery_attempts WHERE contact_id=?", (attempt_id,))
        }
