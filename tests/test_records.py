def test_snapshot_excludes_secret(tmp_path):
    from boss_say_hello.domain import AppConfig, ResumeVersion, RunSnapshot, RunEvent
    from boss_say_hello.storage.records import RecordStore
    store = RecordStore(tmp_path/'db.sqlite3')
    resume = ResumeVersion('v1','hash','original',('Python',),'summary', True)
    run = RunSnapshot('r1','a1',AppConfig(),resume,'2026-10-23T10:00:00+08:00')
    store.save_resume(resume)
    store.save_run(run)
    store.append_event(RunEvent('r1','state',{'state':'ready'}))
    assert store.get_resume('v1') == resume
    assert store.list_runs() == [run]
    assert store.events('r1')[0].payload == {'state':'ready'}
    assert 'api_key' not in store.list_runs()[0].config.__dict__
    store.close()


def test_database_failure_propagates(tmp_path):
    import pytest, sqlite3
    from boss_say_hello.domain import RunEvent
    from boss_say_hello.storage.records import RecordStore
    store = RecordStore(tmp_path/'db.sqlite3')
    store.close()
    with pytest.raises(sqlite3.ProgrammingError): store.append_event(RunEvent('r','error',{}))
