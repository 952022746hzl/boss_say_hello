from dataclasses import replace

import pytest

from boss_say_hello.domain import AppConfig, Job, ResumeVersion, RunSnapshot


@pytest.fixture
def config():
    return lambda **kw: replace(AppConfig(base_url="https://model.invalid/v1", model="test-model"), **kw)


@pytest.fixture
def resume():
    return lambda **kw: replace(
        ResumeVersion("v1", "hash", "Python engineer project", ("Python",), "Built a Python service", True),
        **kw,
    )


@pytest.fixture
def job():
    return lambda **kw: replace(
        Job(
            "a1",
            "j1",
            "hr1",
            "Python engineer",
            "Example",
            "北京",
            "20-30K",
            "Python service development",
            "https://www.zhipin.com/job_detail/j1.html",
            "new",
        ),
        **kw,
    )


@pytest.fixture
def snapshot(config, resume):
    return lambda **kw: replace(
        RunSnapshot("r1", "a1", config(), resume(), "2026-10-23T10:00:00+08:00"), **kw
    )


@pytest.fixture
def storage(tmp_path):
    from boss_say_hello.storage.records import RecordStore

    store = RecordStore(tmp_path / "records.sqlite3")
    yield store
    store.close()
