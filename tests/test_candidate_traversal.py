import json
import re
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
from boss_say_hello.browser.port import BrowserFailure, SessionInfo
from boss_say_hello.runtime.control import RunControl
from tests.test_agent_engine import environment, prepare


def card(jid, trace="one"):
    return {"url": f"https://www.zhipin.com/job_detail/{jid}.html?trace={trace}", "title": jid}


@pytest.fixture
def adapter(monkeypatch):
    browser = NodriverAdapter()
    browser.bind_control(RunControl())
    browser.session = AsyncMock(return_value=SessionInfo("a1", True))
    monkeypatch.setattr("boss_say_hello.browser.nodriver_adapter.asyncio.sleep", AsyncMock())
    return browser


async def test_duplicate_urls_in_one_batch_do_not_consume_candidate_slots(adapter):
    adapter.evaluate = AsyncMock(
        return_value={"cards": [card("j1", str(i)) for i in range(6)] + [card("j2")], "ended": True}
    )
    result = await adapter.candidate_batch()
    assert [job.id for job in result["jobs"]] == ["j1", "j2"]
    assert result["ended"]


async def test_changed_tracking_urls_scroll_past_seen_jobs_to_a_new_job(adapter):
    from boss_say_hello.browser import selectors as dom

    scrolled = False
    reads = 0

    async def evaluate(tab, code):
        nonlocal scrolled, reads
        if code == dom.SCROLL:
            scrolled = True
            return True
        assert code == dom.CARDS
        reads += 1
        return {
            "cards": [card(f"j{i}", str(reads)) for i in range(5)] + ([card("new")] if scrolled else []),
            "ended": scrolled,
        }

    adapter.evaluate = evaluate
    assert len((await adapter.candidate_batch())["jobs"]) == 5
    result = await adapter.candidate_batch()
    assert [job.id for job in result["jobs"]] == ["new"]
    assert result["ended"]


async def test_repeated_cards_without_load_evidence_are_not_reported_as_end(adapter):
    from boss_say_hello.browser import selectors as dom

    adapter.poll_attempts = 2
    adapter.candidate_seen.add("j1")

    async def evaluate(tab, code):
        return True if code == dom.SCROLL else {"cards": [card("j1", "changed")], "ended": False}

    adapter.evaluate = evaluate
    with pytest.raises(BrowserFailure, match="未继续加载"):
        await adapter.candidate_batch()


async def test_temporarily_missing_job_is_requeued_without_stale_context(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="j2")], "ended": True}
    await prepare(ex)
    ex.done.add("j1")
    ex.details.add("j2")
    ex.assessments["j2"] = object()
    ex.greetings["j2"] = object()
    browser.current_candidate_ids = AsyncMock(return_value={"elsewhere"})
    await ex.sync_refreshed_candidates(ex.receipt, "j1")
    assert ex.jobs == {}
    assert ex.done == {"j1"}
    assert "j2" not in ex.details and "j2" not in ex.greetings and "j2" not in ex.assessments
    browser.candidate_batch.return_value = {"jobs": [job(), job(id="j2")], "ended": True}
    result = await ex.execute("list_jobs", {"reason": "暂缺岗位再次出现"})
    assert [j["id"] for j in result["jobs"]] == ["j2"]
    assert ex.active_job_id() == "j2"
    assert ex.discovered_count() == 2


async def test_reappearing_unhandled_job_still_checks_persistent_contact_history(
    config, snapshot, job, storage, tmp_path
):
    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    browser.current_candidate_ids = AsyncMock(return_value={"elsewhere"})
    await ex.sync_refreshed_candidates(ex.receipt)
    assert ex.done == set()
    ex.journal.reserve(ex.run, job())  # An unresolved attempt must block re-sending.
    result = await ex.execute("list_jobs", {"reason": "再次核验已发现岗位"})
    assert result["jobs"] == []
    assert ex.active_job_id() is None
    assert ex.done == {"j1"}


async def test_unavailable_after_rediscovery_is_not_retried_indefinitely(
    config, snapshot, job, storage, tmp_path
):
    from boss_say_hello.browser.port import JobUnavailable

    ex, browser, *_ = environment(config, snapshot, job, storage, tmp_path)
    await prepare(ex)
    browser.current_candidate_ids = AsyncMock(return_value={"elsewhere"})
    await ex.sync_refreshed_candidates(ex.receipt)
    await ex.execute("list_jobs", {"reason": "重新发现岗位"})
    browser.refresh_job.side_effect = JobUnavailable("岗位已下架")
    result = await ex.execute("read_job", {"job_id": "j1", "reason": "重新核验"})
    assert result["code"] == "job_unavailable"
    assert ex.done == {"j1"}
    result = await ex.execute("list_jobs", {"reason": "继续其他岗位"})
    assert result["jobs"] == [] and result["ended"]
    assert ex.active_job_id() is None


async def test_released_candidate_is_returned_again_by_browser(adapter):
    adapter.evaluate = AsyncMock(return_value={"cards": [card("j1")], "ended": True})
    await adapter.candidate_batch()
    adapter.release_candidates({"j1"})
    assert [j.id for j in (await adapter.candidate_batch())["jobs"]] == ["j1"]


class ReturningListPage:
    """DOM boundary model: navigation loses loaded rows and scrolling loads more."""

    def __init__(self, *, load=True):
        self.path = "/web/geek/job-recommend"
        self.document = "before"
        self.top = 1600
        self.loaded = 3
        self.load = load
        self.reloads = 0
        self.scrolls = 0
        self.activated = False

    async def get(self, url):
        self.reloads += 1
        self.path = "/web/geek/job-recommend"
        self.document = "after"
        self.top = 0
        self.loaded = 1

    async def activate(self):
        self.activated = True

    async def evaluate(self, code):
        if code.startswith("/* LIST_LOCATION */"):
            return {"path": self.path}
        if code.startswith("/* LIST_PROGRESS */"):
            return {
                "document": self.document,
                "scroll_top": self.top,
                "anchors": [card(f"row{self.loaded}")["url"]],
                "loaded_count": self.loaded,
                "ended": False,
            }
        if code.startswith("/* RESTORE_LIST_PROGRESS */"):
            saved = json.loads(re.search(r"const saved=(.*);", code).group(1))
            self.top = min(saved["scroll_top"], (self.loaded - 1) * 800)
            return True
        if code.startswith("/* SCROLL */"):
            self.scrolls += 1
            if self.load:
                self.loaded += 1
                self.top = (self.loaded - 1) * 800
            return True
        if '"action": "contact"' in code:
            return {"clicked": False, "code": "target_changed"}
        raise AssertionError(code[:100])


async def test_return_reloads_and_restores_loaded_list_position(adapter, job):
    page = ReturningListPage()
    adapter.list_tab = adapter.detail_tab = page
    adapter.preference_snapshot = AsyncMock(return_value={"expectations": [{"selected": True}]})
    await adapter.open_contact(job())
    page.path = "/web/geek/chat"
    assert await adapter.return_to_list()
    assert page.reloads == 1 and page.top == 1600 and page.loaded >= 3
    assert page.activated


async def test_return_keeps_existing_list_without_reloading(adapter, job):
    page = ReturningListPage()
    adapter.list_tab = adapter.detail_tab = page
    await adapter.open_contact(job())
    assert await adapter.return_to_list()
    assert page.reloads == 0 and page.top == 1600 and page.scrolls == 0


async def test_return_does_not_claim_restoration_when_list_stops_loading(adapter, job):
    page = ReturningListPage(load=False)
    adapter.list_tab = adapter.detail_tab = page
    adapter.preference_snapshot = AsyncMock(return_value={"expectations": [{"selected": True}]})
    await adapter.open_contact(job())
    page.path = "/web/geek/chat"
    assert not await adapter.return_to_list()
    assert 0 < page.scrolls <= adapter.poll_attempts
    assert not page.activated


@pytest.mark.parametrize("read_failure", [False, True])
async def test_progress_capture_failure_is_known_not_to_have_attempted_contact(adapter, job, read_failure):
    adapter.list_tab = adapter.detail_tab = object()
    calls = []

    async def evaluate(tab, code):
        calls.append(code)
        if read_failure:
            raise BrowserFailure("读取列表失败")
        return {"anchors": [], "scroll_top": 0}

    adapter.evaluate = evaluate
    result = await adapter.open_contact(job())
    assert result.outcome == "failed" and result.attempted is False
    assert not any('"action": "contact"' in code for code in calls)


@pytest.mark.parametrize("window_scroll", [False, True])
def test_progress_selectors_restore_actual_dom_scroll_root_without_browser(window_scroll):
    from boss_say_hello.browser import selectors as dom

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for this browser-free JavaScript fixture")
    saved = {
        "document": "123",
        "scroll_top": 1000,
        "anchors": ["https://www.zhipin.com/job_detail/j2.html?trace=before"],
        "anchor_offset": 0,
        "loaded_count": 5,
    }
    payload = {
        "windowScroll": window_scroll,
        "snapshot": dom.LIST_PROGRESS,
        "scroll": dom.SCROLL,
        "restore": dom.restore_list_progress(saved),
    }
    script = r"""
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const body={};
let top=1000;
const viewportTop=input.windowScroll?0:100;
const root={parentElement:body,clientHeight:500,scrollHeight:3000,
  getBoundingClientRect:()=>({top:viewportTop})};
Object.defineProperty(root,'scrollTop',{
  get:()=>top,set:value=>top=Math.max(0,Math.min(value,root.scrollHeight-root.clientHeight))
});
let cards=Array.from({length:5},(_,index)=>({
  parentElement:root,index,
  querySelector:()=>({href:`https://www.zhipin.com/job_detail/j${index}.html?trace=after`}),
  getBoundingClientRect(){return {top:viewportTop+this.index*500-top,bottom:viewportTop+(this.index+1)*500-top};},
  getClientRects:()=>[{}]
}));
const document={body,documentElement:root,scrollingElement:input.windowScroll?root:{},
  querySelector:()=>cards[0],querySelectorAll:selector=>selector==='.job-card-box'?cards:[]};
const getComputedStyle=()=>({overflowY:'auto'});
const performance={timeOrigin:123};
const run=code=>JSON.parse(eval(code));
const before=run(input.snapshot);
run(input.scroll);
const advanced=top;
top=0;
run(input.restore);
const restored=run(input.snapshot);
// A missing saved card must use scroll depth and not require the removed anchor.
cards.splice(2,1);
cards.forEach((card,index)=>card.index=index);
top=0;
run(input.restore);
const missingAnchor=top;
process.stdout.write(JSON.stringify({before,advanced,restored,missingAnchor}));
"""
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    observed = json.loads(result.stdout)
    assert observed["before"]["scroll_top"] == 1000
    assert observed["before"]["anchors"] == ["https://www.zhipin.com/job_detail/j2.html?trace=after"]
    assert observed["advanced"] == 1600
    assert observed["restored"]["scroll_top"] == 1000
    assert observed["restored"]["anchors"] == observed["before"]["anchors"]
    assert observed["missingAnchor"] == 1000
