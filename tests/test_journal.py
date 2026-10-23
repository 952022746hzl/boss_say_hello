def test_crash_after_intent_is_unknown(snapshot, job, storage):
    from boss_say_hello.delivery.journal import DeliveryJournal

    journal = DeliveryJournal(storage)
    run = snapshot()
    attempt = journal.reserve(run, job())
    journal.begin_stage(attempt, "contact")
    journal.recover_incomplete()
    assert not journal.eligible(run.account_id, job().id)
    assert journal.contact_count(run.id) == 1
    assert journal.stages(attempt)["contact"]["outcome"] == "unknown"


def test_duplicate_reservation_is_atomic(snapshot, job, storage):
    from boss_say_hello.delivery.journal import DeliveryJournal

    journal = DeliveryJournal(storage)
    assert journal.reserve(snapshot(), job())
    assert journal.reserve(snapshot(id="r2"), job()) is None


def test_cancel_before_contact_releases_slot(snapshot, job, storage):
    from boss_say_hello.delivery.journal import DeliveryJournal

    journal = DeliveryJournal(storage)
    attempt = journal.reserve(snapshot(), job())
    journal.cancel_before_contact(attempt)
    assert journal.contact_count("r1") == 0 and journal.eligible("a1", "j1")
