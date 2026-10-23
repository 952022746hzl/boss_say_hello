from unittest.mock import Mock

import pytest


def test_dirty_resume_blocks_start(qtbot, config, resume):
    from boss_say_hello.runtime.controller import Controller
    from boss_say_hello.runtime.worker import WorkerThread

    worker = WorkerThread()
    worker.start()
    c = Controller(worker)
    try:
        c.current_resume = resume()
        c.resume_ready = True
        c.invalidate_resume()
        with pytest.raises(ValueError, match="确认"):
            c.start_run(config())
    finally:
        c.close()
        assert worker.wait(3000)


async def test_backend_restart_never_starts_jobs(tmp_path):
    from boss_say_hello.runtime.backend import Backend
    from tests.fakes.browser import FakeBrowser

    events = []
    b = Backend(events.append, root=tmp_path, browser=FakeBrowser())
    await b.initialize()
    assert not b.config.auto_send and not b.config.send_attachment
    assert not b.browser.calls
    await b.close()


async def test_secret_is_fixed_for_run(tmp_path, config, resume, monkeypatch):
    from unittest.mock import AsyncMock

    from boss_say_hello.runtime.backend import Backend
    from boss_say_hello.runtime.control import RunControl
    from tests.fakes.browser import FakeBrowser

    b = Backend(lambda e: None, root=tmp_path, browser=FakeBrowser())
    await b.initialize()
    b.secrets["default-model"] = "first"
    created = []

    def ai_factory(config, secret):
        created.append(secret)
        b.secrets["default-model"] = "second"
        return Mock(close=AsyncMock())

    monkeypatch.setattr("boss_say_hello.runtime.backend.AiService", ai_factory)
    await b.run(config(), resume(), RunControl())
    assert created == ["first"] and b.secrets["default-model"] == "second"
    await b.close()
