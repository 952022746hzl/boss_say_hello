from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from tests.test_agent_engine import environment, prepare


async def test_background_uses_one_combined_query_and_duplicate_reuses_result(
    config, snapshot, job, storage, tmp_path
):
    ex, _, search, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    await ex.execute("read_job", {"job_id": "j1", "reason": "详情"})
    first = await ex.execute("search_company", {"job_id": "j1", "reason": "一次综合背调"})
    second = await ex.execute("search_company", {"job_id": "j1", "reason": "重复请求"})
    assert first["sources"] == second["sources"]
    assert search.search.await_count == 1
    assert "j1" in ex.company_checked


async def test_another_job_at_same_company_reuses_overview(config, snapshot, job, storage, tmp_path):
    ex, _, search, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    ex.jobs["j2"] = job(id="j2")
    for jid in ("j1", "j2"):
        await ex.execute("search_company", {"job_id": jid, "reason": "综合查询"})
        await ex.execute("skip_job", {"job_id": jid, "reason": "当前岗位处理结束"})
    assert search.search.await_count == 1
    assert ex.sources["j1"] == ex.sources["j2"]


@pytest.mark.parametrize("failed", [False, True])
async def test_empty_or_failed_query_does_not_repeat_for_same_company(
    config, snapshot, job, storage, tmp_path, failed
):
    ex, _, search, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    search.search.return_value = []
    if failed:
        search.search.side_effect = RuntimeError("search unavailable")
        with pytest.raises(RuntimeError):
            await ex.search_company("j1")
    else:
        assert (await ex.search_company("j1"))["status"] == "pending"
    assert (await ex.search_company("j1"))["status"] == "pending"
    ex.jobs["j2"] = job(id="j2")
    assert (await ex.search_company("j2"))["status"] == "pending"
    assert search.search.await_count == 1


def test_old_query_limit_migrates_without_losing_settings(config):
    from boss_say_hello.domain import config_from_dict

    original = config(target_title="Java", cities=("深圳",))
    saved = {**asdict(original), "max_company_searches": 3}
    assert config_from_dict(saved) == original


async def test_missing_company_link_reuses_company_across_job_urls(config, snapshot, job, storage, tmp_path):
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    ex, browser, search, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    adapter = NodriverAdapter()
    adapter.read_detail = AsyncMock(return_value={"company_id": "co1", "company": "Example"})
    browser.company_info = adapter.company_info
    ex.jobs["j2"] = job(id="j2", url="https://www.zhipin.com/job_detail/second.html")
    await ex.search_company("j1")
    await ex.search_company("j2")
    assert search.search.await_count == 1
