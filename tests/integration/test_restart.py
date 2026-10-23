from boss_say_hello.delivery.journal import DeliveryJournal
from boss_say_hello.storage.records import RecordStore
from boss_say_hello.storage.settings import ConfigStore


def test_restart_unknown_and_switches_off(tmp_path, snapshot, config, job):
    settings = ConfigStore(tmp_path)
    settings.save(config(auto_send=True, send_attachment=True, attachment_id="resume1"))
    path = tmp_path / "records.sqlite3"
    store = RecordStore(path)
    journal = DeliveryJournal(store)
    run = snapshot()
    store.save_run(run)
    attempt = journal.reserve(run, job())
    journal.begin_stage(attempt, "contact")
    store.close()
    reopened = RecordStore(path)
    journal = DeliveryJournal(reopened)
    journal.recover_incomplete()
    assert not settings.load().auto_send and not settings.load().send_attachment
    assert not journal.eligible("a1", "j1") and journal.stages(attempt)["contact"]["outcome"] == "unknown"
    assert reopened.list_runs()[0] == run
    reopened.close()
