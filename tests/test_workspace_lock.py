import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from boss_say_hello.runtime.backend import Backend
from tests.fakes.browser import FakeBrowser


def backend(root, emit=None, browser=None):
    return Backend(emit or (lambda event: None), root=root, browser=browser or FakeBrowser())


async def test_second_backend_cannot_recover_another_instances_pending_reservation(
    tmp_path, snapshot, config, job
):
    first = backend(tmp_path)
    events = []
    second = backend(tmp_path, events.append)
    try:
        await first.initialize()
        run = snapshot(config=config(auto_send=True))
        attempt = first.journal.reserve(run, job())
        first.journal.begin_stage(attempt, "contact")
        log_before = first.log.path.read_bytes()

        with pytest.raises(ValueError, match="工作目录.*正在使用"):
            await second.initialize()

        assert first.journal.stages(attempt)["contact"]["outcome"] == "intent"
        assert first.journal.company_limit_reason(run, job(id="other-job"))
        assert first.log.path.read_bytes() == log_before
        assert second.store is None and second.log is None
        assert events == []
    finally:
        await second.close()
        await first.close()


async def test_different_workspaces_can_initialize_together(tmp_path):
    first, second = backend(tmp_path / "one"), backend(tmp_path / "two")
    try:
        await first.initialize()
        await second.initialize()
        assert first.initialized and second.initialized
    finally:
        await second.close()
        await first.close()


async def test_close_allows_same_backend_and_another_backend_to_reopen(tmp_path):
    first, second = backend(tmp_path), backend(tmp_path)
    try:
        await first.initialize()
        await first.close()
        await second.initialize()
        assert second.initialized
        await second.close()
        await first.initialize()
        assert first.store.list_runs() == []
    finally:
        await second.close()
        await first.close()


@pytest.mark.parametrize("stage", ["EventLog", "ConfigStore", "RecordStore", "recover_incomplete"])
async def test_initialization_failure_closes_resources_and_releases_workspace(tmp_path, monkeypatch, stage):
    from boss_say_hello.delivery.journal import DeliveryJournal
    from boss_say_hello.runtime import backend as module

    failed, next_instance = backend(tmp_path), backend(tmp_path)
    resources = {}

    def fail(*args, **kwargs):
        resources["store"] = failed.store
        resources["log"] = failed.log
        raise OSError("injected initialization failure")

    try:
        with monkeypatch.context() as patch:
            if stage == "recover_incomplete":
                patch.setattr(DeliveryJournal, stage, fail)
            else:
                patch.setattr(module, stage, fail)
            with pytest.raises(OSError, match="injected initialization failure"):
                await failed.initialize()

        assert not failed.initialized
        if resources["log"]:
            assert resources["log"].closed
        if resources["store"]:
            import sqlite3

            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                resources["store"].list_runs()
        await next_instance.initialize()
        assert next_instance.initialized
    finally:
        await next_instance.close()
        await failed.close()


async def test_workspace_remains_locked_while_browser_close_is_pending(tmp_path):
    closing, release = asyncio.Event(), asyncio.Event()

    class ClosingBrowser(FakeBrowser):
        async def close(self):
            closing.set()
            await release.wait()

    first = backend(tmp_path, browser=ClosingBrowser())
    second = backend(tmp_path)
    await first.initialize()
    close_task = asyncio.create_task(first.close())
    try:
        await asyncio.wait_for(closing.wait(), 3)
        with pytest.raises(ValueError, match="工作目录.*正在使用"):
            await second.initialize()
    finally:
        release.set()
        await close_task
        await second.close()
    await second.initialize()
    await second.close()


_CHILD = """
import asyncio
import sys
from pathlib import Path
from boss_say_hello.runtime.backend import Backend
from tests.fakes.browser import FakeBrowser

async def main():
    instance = Backend(lambda event: None, root=Path(sys.argv[1]), browser=FakeBrowser())
    await instance.initialize()
    print('LOCKED', flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
"""


async def test_second_process_cannot_take_workspace_until_owner_exits(tmp_path):
    project = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(project / "src"), str(project)))}
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(tmp_path),
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = backend(tmp_path)
    try:
        assert (await asyncio.wait_for(child.stdout.readline(), 15)).strip() == b"LOCKED"
        with pytest.raises(ValueError, match="工作目录.*正在使用"):
            await second.initialize()
        assert second.store is None
        child.kill()
        await asyncio.wait_for(child.wait(), 10)
        # Killing the owner bypasses close(), so startup must recover the stale lock.
        await second.initialize()
        assert second.initialized
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        await second.close()
