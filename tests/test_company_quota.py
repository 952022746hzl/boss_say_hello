import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from threading import Barrier

import pytest

from boss_say_hello.browser.port import ActionResult
from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.storage.records import RecordStore


class Clock:
    def __init__(self, value="2026-10-28T10:00:00+08:00"):
        self.value = value

    def now(self):
        return datetime.fromisoformat(self.value)


def finish(journal, attempt, stage, outcome="succeeded"):
    journal.begin_stage(attempt, stage)
    journal.finish_stage(attempt, stage, ActionResult(outcome, "verified"))


def succeed(journal, attempt):
    assert attempt
    for stage in ("contact", "message"):
        journal.begin_stage(attempt, stage)
        journal.finish_stage(attempt, stage, ActionResult("succeeded", "verified"))


def test_verified_success_blocks_same_company_other_job_in_later_run(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    succeed(journal, journal.reserve(run, job(company_id="co1")))

    assert journal.reserve(snapshot(id="r2", config=run.config), job(id="j2", company_id="co1")) is None
    assert journal.eligible("a1", "j2")


def test_quota_is_scoped_to_account_and_company(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    succeed(journal, journal.reserve(run, job(company_id="co1")))
    assert journal.reserve(run, job(id="j2", company_id="co2"))
    assert journal.reserve(
        snapshot(id="r2", account_id="a2", config=run.config),
        job(account_id="a2", id="j3", company_id="co1"),
    )


def test_configurable_cap_counts_success_and_pending_reservations(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True, max_company_daily_contacts=2))
    succeed(journal, journal.reserve(run, job(company_id="co1")))
    pending = journal.reserve(run, job(id="j2", company_id="co1"))
    assert pending
    reason = journal.company_limit_reason(run, job(id="j3", company_id="co1"))
    assert reason and "2" in reason and "预留" in reason
    assert journal.reserve(run, job(id="j3", company_id="co1")) is None
    journal.cancel_before_contact(pending)
    assert journal.reserve(run, job(id="j3", company_id="co1"))


def test_restart_preserves_verified_company_success(tmp_path, snapshot, config, job):
    path = tmp_path / "quota.sqlite3"
    clock = Clock()
    run = snapshot(config=config(auto_send=True))
    with_store = RecordStore(path)
    journal = DeliveryJournal(with_store, now=clock.now)
    succeed(journal, journal.reserve(run, job(company_id="co1")))
    with_store.close()
    reopened = RecordStore(path)
    try:
        journal = DeliveryJournal(reopened, now=clock.now)
        journal.recover_incomplete()
        assert journal.reserve(snapshot(id="r2", config=run.config), job(id="j2", company_id="co1")) is None
        assert not journal.eligible("a1", "j1")
    finally:
        reopened.close()


def test_success_uses_actual_beijing_day_and_not_run_or_reservation_time(storage, snapshot, config, job):
    clock = Clock("2026-10-28T15:59:59+00:00")
    journal = DeliveryJournal(storage, now=clock.now)
    run = snapshot(config=config(auto_send=True), created_at="2026-10-27T09:00:00+08:00")
    attempt = journal.reserve(run, job(company_id="co1"))
    finish(journal, attempt, "contact")
    clock.value = "2026-10-28T16:00:01+00:00"
    finish(journal, attempt, "message")
    assert journal.company_limit_reason(run, job(id="j2", company_id="co1"))
    clock.value = "2026-10-29T16:00:00+00:00"
    assert journal.company_limit_reason(run, job(id="j2", company_id="co1")) is None
    assert journal.reserve(run, job(id="j2", company_id="co1"))
    assert not journal.eligible("a1", "j1")


def test_in_flight_reservation_remains_protected_across_midnight(storage, snapshot, config, job):
    clock = Clock("2026-10-28T23:59:59+08:00")
    journal = DeliveryJournal(storage, now=clock.now)
    run = snapshot(config=config(auto_send=True))
    attempt = journal.reserve(run, job(company_id="co1"))
    clock.value = "2026-10-29T00:00:01+08:00"
    assert journal.reserve(run, job(id="j2", company_id="co1")) is None
    succeed(journal, attempt)
    assert journal.reserve(run, job(id="j2", company_id="co1")) is None


@pytest.mark.parametrize(
    "stage,outcome",
    [("contact", "failed"), ("contact", "unknown"), ("message", "failed"), ("message", "unknown")],
)
def test_unsuccessful_attempt_releases_company_but_keeps_job_dedup(
    storage, snapshot, config, job, stage, outcome
):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    attempt = journal.reserve(run, job(company_id="co1"))
    if stage == "message":
        finish(journal, attempt, "contact")
    finish(journal, attempt, stage, outcome)
    assert journal.success_count(run.id) == 0
    assert journal.reserve(run, job(company_id="co1")) is None
    assert journal.reserve(run, job(id="j2", company_id="co1"))


def test_contact_only_and_message_only_are_not_company_successes(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    contact_only = journal.reserve(run, job(company_id="co1"))
    finish(journal, contact_only, "contact")
    journal.release_company_reservation(contact_only)
    message_only = journal.reserve(run, job(id="j2", company_id="co1"))
    finish(journal, message_only, "message")
    assert journal.success_count(run.id) == 0
    assert journal.reserve(run, job(id="j3", company_id="co1"))


def test_recovery_releases_pending_quota_without_retrying_interrupted_job(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    attempt = journal.reserve(run, job(company_id="co1"))
    journal.begin_stage(attempt, "contact")
    journal.recover_incomplete()
    assert journal.stages(attempt)["contact"]["outcome"] == "unknown"
    assert not journal.eligible("a1", "j1")
    assert journal.reserve(run, job(id="j2", company_id="co1"))


def test_preview_ignores_company_quota_and_missing_identity(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    succeed(journal, journal.reserve(run, job(company_id="co1")))
    preview = replace(run, config=replace(run.config, auto_send=False))
    assert journal.company_limit_reason(preview, job(id="j2", company_id="co1")) is None
    assert journal.company_limit_reason(preview, job(company_id="", company="品牌简称")) is None


def test_missing_company_identity_requires_confirmation_without_consuming_job(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    current = job(company_id="", company="品牌简称")
    reason = journal.company_limit_reason(run, current)
    assert reason and "公司身份" in reason and "待确认" in reason
    assert journal.reserve(run, current) is None
    assert journal.eligible("a1", current.id)


@pytest.mark.parametrize(
    "first,next_job",
    [
        (
            {"company_id": "syntheticCompany9~"},
            {"company_id": "", "company_url": "https://www.zhipin.com/gongsi/syntheticCompany9~.html"},
        ),
        (
            {"company_id": "syntheticCompany9~~"},
            {"company_id": "", "company_url": "https://www.zhipin.com/gongsi/syntheticCompany9~~.html"},
        ),
        (
            {"company_id": "co1"},
            {"company_id": "", "company_url": "https://www.zhipin.com/gongsi/co1.html?src=card"},
        ),
        (
            {"company_id": "", "company_full_name": "北京示例科技有限公司"},
            {"company_id": "", "company": "北京示例科技有限公司"},
        ),
        (
            {"company_id": "", "company_full_name": "北京示例科技有限公司"},
            {"company_id": "co1", "company_full_name": "北京示例科技有限公司"},
        ),
    ],
)
def test_verified_company_identifiers_match_without_brand_fuzzy_matching(
    storage, snapshot, config, job, first, next_job
):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    succeed(journal, journal.reserve(run, job(**first)))
    assert journal.reserve(run, job(id="j2", **next_job)) is None


def test_distinct_platform_ids_are_not_merged_by_brand_or_company_name(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    first = job(company_id="co1", company="示例", company_full_name="示例科技有限公司")
    succeed(journal, journal.reserve(run, first))
    assert journal.reserve(run, replace(first, id="j2", company_id="co2"))


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/gongsi/co1.html",
        "https://www.zhipin.com.evil.example/gongsi/co1.html",
        "https://user@www.zhipin.com/gongsi/co1.html",
        "https://www.zhipin.com/job_detail/co1.html",
        "https://www.zhipin.com/gongsi/co1.html/other",
        "https://www.zhipin.com/gongsi/%63o1.html",
        "https://www.zhipin.com:99999/gongsi/co1.html",
    ],
)
def test_unverified_company_url_does_not_manufacture_identity(storage, snapshot, config, job, url):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    assert journal.reserve(run, job(company_id="", company="简称", company_url=url)) is None


@pytest.mark.parametrize("mixed_identity", [False, True])
def test_reservation_rechecks_atomically_across_connections(tmp_path, snapshot, config, job, mixed_identity):
    path = tmp_path / "race.sqlite3"
    initial = RecordStore(path)
    initial.close()
    barrier = Barrier(2)
    run = snapshot(config=config(auto_send=True))

    def reserve(index):
        store = RecordStore(path)
        try:
            journal = DeliveryJournal(store)
            current = job(
                id=f"j{index}",
                company_id="" if mixed_identity and index == 2 else "co1",
                company_full_name="北京示例科技有限公司" if mixed_identity else "",
            )
            assert journal.company_limit_reason(run, current) is None
            barrier.wait(timeout=5)
            return journal.reserve(run, current)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(reserve, (1, 2)))
    assert sum(attempt is not None for attempt in attempts) == 1


def test_legacy_success_keeps_dedup_without_fabricating_success_date(tmp_path, snapshot, config, job):
    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE contacts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, account_id TEXT NOT NULL,
                              job_id TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE delivery_attempts(contact_id TEXT, stage TEXT, outcome TEXT, detail TEXT,
                                       PRIMARY KEY(contact_id, stage));
        INSERT INTO contacts VALUES ('old', 'old-run', 'a1', 'j1', 0);
        INSERT INTO delivery_attempts VALUES ('old', 'contact', 'succeeded', 'verified');
        INSERT INTO delivery_attempts VALUES ('old', 'message', 'succeeded', 'verified');
        PRAGMA user_version=1;
    """)
    db.close()
    store = RecordStore(path)
    try:
        journal = DeliveryJournal(store, now=Clock().now)
        assert journal.success_count("old-run") == 1
        assert not journal.eligible("a1", "j1")
        notice = journal.company_quota_history_notice()
        assert notice and "历史" in notice and "成功时间" in notice
        run = snapshot(config=config(auto_send=True))
        assert journal.reserve(run, job(id="j2", company_id="co1"))
        old = store.db.execute(
            "SELECT succeeded_at,timestamp_status FROM contact_company_metadata WHERE contact_id='old'"
        ).fetchone()
        assert old["succeeded_at"] is None and old["timestamp_status"] == "legacy_unknown"
    finally:
        store.close()


def test_observed_id_alias_blocks_later_job_without_company_name(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    full_name = "北京示例科技有限公司"
    succeed(journal, journal.reserve(run, job(company_id="", company_full_name=full_name)))
    assert journal.company_limit_reason(run, job(id="j2", company_id="co1", company_full_name=full_name))

    assert journal.reserve(run, job(id="j3", company_id="co1", company_full_name="")) is None
    assert journal.eligible("a1", "j3")


def test_observed_company_alias_survives_restart(tmp_path, snapshot, config, job):
    path = tmp_path / "aliases.sqlite3"
    run = snapshot(config=config(auto_send=True))
    full_name = "北京示例科技有限公司"
    store = RecordStore(path)
    journal = DeliveryJournal(store, now=Clock().now)
    succeed(journal, journal.reserve(run, job(company_id="", company_full_name=full_name)))
    assert journal.company_limit_reason(run, job(id="j2", company_id="co1", company_full_name=full_name))
    store.close()

    reopened = RecordStore(path)
    try:
        journal = DeliveryJournal(reopened, now=Clock().now)
        assert journal.reserve(run, job(id="j3", company_id="co1", company_full_name="")) is None
    finally:
        reopened.close()


def test_name_with_conflicting_platform_ids_requires_confirmation(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    full_name = "北京示例科技有限公司"
    for company_id in ("co1", "co2"):
        assert (
            journal.company_limit_reason(run, job(company_id=company_id, company_full_name=full_name)) is None
        )
    current = job(company_id="", company_full_name=full_name)
    reason = journal.company_limit_reason(run, current)
    assert reason and "待确认" in reason
    assert journal.reserve(run, current) is None
    assert journal.eligible("a1", current.id)


def test_ambiguous_name_only_success_cannot_be_assigned_to_either_id(storage, snapshot, config, job):
    clock = Clock()
    journal = DeliveryJournal(storage, now=clock.now)
    run = snapshot(config=config(auto_send=True, max_company_daily_contacts=2))
    full_name = "北京示例科技有限公司"
    succeed(journal, journal.reserve(run, job(company_id="", company_full_name=full_name)))
    assert journal.company_limit_reason(run, job(company_id="co1", company_full_name=full_name)) is None
    reason = journal.company_limit_reason(run, job(company_id="co2", company_full_name=full_name))
    assert reason and "待确认" in reason
    for company_id in ("co1", "co2"):
        assert journal.reserve(run, job(id=f"next-{company_id}", company_id=company_id)) is None
    # Yesterday's success no longer spends today's quota, even if its company
    # remains ambiguous. New ID-backed records can still be safely attributed.
    clock.value = "2026-10-29T10:00:00+08:00"
    assert journal.reserve(run, job(id="tomorrow-co1", company_id="co1"))
    assert journal.reserve(run, job(id="tomorrow-co2", company_id="co2"))


def test_observed_aliases_do_not_leak_between_accounts(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    full_name = "北京示例科技有限公司"
    assert journal.company_limit_reason(run, job(company_id="co1", company_full_name=full_name)) is None
    other = snapshot(id="r2", account_id="a2", config=run.config)
    succeed(journal, journal.reserve(other, job(account_id="a2", company_id="", company_full_name=full_name)))
    assert journal.reserve(other, job(account_id="a2", id="j2", company_id="co1", company_full_name=""))


def test_recorded_id_and_name_cooccurrence_recovers_alias_on_open(tmp_path, snapshot, config, job):
    path = tmp_path / "alias-backfill.sqlite3"
    store = RecordStore(path)
    journal = DeliveryJournal(store, now=Clock().now)
    full_name = "北京示例科技有限公司"
    observation = journal.reserve(
        snapshot(), job(id="observation", company_id="co1", company_full_name=full_name)
    )
    journal.cancel_before_contact(observation)
    store.close()

    reopened = RecordStore(path)
    try:
        journal = DeliveryJournal(reopened, now=Clock().now)
        run = snapshot(id="actual", config=config(auto_send=True))
        succeed(journal, journal.reserve(run, job(company_id="", company_full_name=full_name)))
        assert journal.reserve(run, job(id="j2", company_id="co1", company_full_name="")) is None
    finally:
        reopened.close()


def test_failed_reservation_rolls_back_alias_and_contact_together(storage, snapshot, config, job):
    journal = DeliveryJournal(storage)
    run = snapshot(config=config(auto_send=True))
    storage.db.execute("""CREATE TRIGGER reject_metadata BEFORE INSERT ON contact_company_metadata
                          BEGIN SELECT RAISE(ABORT, 'metadata unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="metadata unavailable"):
        journal.reserve(run, job(company_id="co1", company_full_name="北京示例科技有限公司"))
    assert journal.eligible("a1", "j1")
    assert storage.db.execute("SELECT COUNT(*) FROM company_identity_aliases").fetchone()[0] == 0
