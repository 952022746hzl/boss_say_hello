import asyncio
import json
from contextlib import asynccontextmanager

import httpx

from boss_say_hello.agent.search import CompanySearch
from boss_say_hello.ai.client import AiService
from boss_say_hello.diagnostics import EventLog, scope
from tests.fakes.clock import FakeClock


class InjectedGate:
    """An injected concurrency boundary, independent of production gate internals."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.waiting = asyncio.Event()

    @asynccontextmanager
    async def slot(self, control=None):
        if self.lock.locked():
            self.waiting.set()
        async with self.lock:
            yield


async def test_model_and_search_use_the_same_injected_request_gate(config):
    model_started = asyncio.Event()
    release_model = asyncio.Event()
    search_started = asyncio.Event()

    async def respond(request):
        if request.url.path.endswith("/search_pro"):
            search_started.set()
            return httpx.Response(200, json={"search_results": []})
        model_started.set()
        await release_model.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    cfg = config(base_url="https://api.moonshot.cn/v1")
    gate = InjectedGate()
    ai = AiService(cfg, "synthetic-test-key", httpx.MockTransport(respond))
    search = CompanySearch(cfg, "synthetic-test-key", httpx.MockTransport(respond))
    ai.request_gate = search.request_gate = gate
    first = second = None
    try:
        first = asyncio.create_task(ai.test_connection())
        await asyncio.wait_for(model_started.wait(), 2)
        second = asyncio.create_task(search.search({"name": "合成公司"}))
        await asyncio.sleep(0.03)
        assert gate.waiting.is_set(), "second client must enter the shared gate"
        assert not search_started.is_set(), "search must wait until model request completes"
        release_model.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert search_started.is_set()
    finally:
        release_model.set()
        await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
        await ai.close()
        await search.close()


async def test_full_model_payload_matches_actual_http_json_and_retries_share_trace(config, tmp_path):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0.1"},
                json={"error": {"type": "engine_overloaded_error", "message": "private-error"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            },
        )

    log = EventLog(tmp_path / "logs")
    ai = AiService(config(), "synthetic-auth-key", httpx.MockTransport(respond))
    ai.recovery.bind(clock=FakeClock())
    try:
        with log.bind(), scope(run_id="r1", job_id="j1", step=3):
            await ai.completion(
                [{"role": "user", "content": "完整简历上下文\n" * 1200}],
                stage="assessment",
                response_format={"type": "json_object"},
            )
        paths = list((log.root / "ai_requests").glob("*.json"))
        assert len(paths) == 1, "one logical request must have one complete request file"
        data = json.loads(paths[0].read_text(encoding="utf-8"))
        assert data["request"] == requests[0] == requests[1]
        assert len(data["attempts"]) == 2
        assert data["context"]["run_id"] == "r1" and data["context"]["job_id"] == "j1"
        assert data["context"]["process_id"] > 0
        serialized = paths[0].read_text(encoding="utf-8") + log.path.read_text(encoding="utf-8")
        assert "synthetic-auth-key" not in serialized and "private-error" not in serialized
        actual = [e for e in log.tail() if e["event"] == "ai.http.started"]
        assert len(actual) == 2
        assert {e["fields"]["request_id"] for e in actual} == {data["request_id"]}
    finally:
        await ai.close()
        log.close()


async def test_company_search_logs_complete_request_with_same_actual_json(config, tmp_path):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"search_results": []})

    log = EventLog(tmp_path / "logs")
    search = CompanySearch(
        config(base_url="https://api.moonshot.cn/v1"), "synthetic-auth-key", httpx.MockTransport(respond)
    )
    try:
        with log.bind():
            await search.search({"name": "合成公司", "city": "北京"})
        paths = list((log.root / "ai_requests").glob("*.json"))
        assert len(paths) == 1
        data = json.loads(paths[0].read_text(encoding="utf-8"))
        assert data["request"] == requests[0]
        assert data["stage"] == "company_search_pro"
        assert len(data["attempts"]) == 1
    finally:
        await search.close()
        log.close()
