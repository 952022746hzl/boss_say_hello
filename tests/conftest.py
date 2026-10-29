from dataclasses import replace

import pytest

from boss_say_hello.domain import AppConfig, Job, ResumeVersion, RunSnapshot
from tests.fakes.agent import make_agent


@pytest.fixture
def config():
    # Shared scenario fixtures use preview explicitly; delivery tests opt in.
    return lambda **kw: replace(
        AppConfig(base_url="https://model.invalid/v1", model="test-model", auto_send=False), **kw
    )


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
            company_id="co1",
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


@pytest.fixture
def agent_config(config):
    return lambda **kw: config(
        **{
            "target_title": "Python engineer",
            "cities": ("北京",),
            "salary_min": 20000,
            "salary_max": 30000,
            **kw,
        }
    )


@pytest.fixture
def setup_agent(storage, tmp_path, job, snapshot, agent_config):
    def make(run=None, jobs=None):
        return make_agent(
            run or snapshot(config=agent_config()),
            [job()] if jobs is None else jobs,
            storage,
            tmp_path,
        )

    return make
