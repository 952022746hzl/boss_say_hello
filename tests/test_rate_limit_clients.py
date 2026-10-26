import json

import httpx
import pytest

from boss_say_hello.agent.search import CompanySearch
from boss_say_hello.ai.client import AiService
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.diagnostics import EventLog
from boss_say_hello.domain import Assessment
from tests.fakes.clock import FakeClock

PRIVATE_MESSAGE = "SYNTHETIC_PRIVATE_PROVIDER_MESSAGE"
PRIVATE_BODY = "SYNTHETIC_PRIVATE_RESPONSE_BODY"
PRIVATE_PROMPT = "SYNTHETIC_PRIVATE_PROMPT"
API_KEY = "synthetic-key-never-log"


@pytest.fixture
def event_log(tmp_path):
    log = EventLog(tmp_path / "logs")
    try:
        yield log
    finally:
        log.close()


def limited_response(code="rate_limit_reached_error"):
    return httpx.Response(
        429,
        headers={"retry-after": "0.25"},
        json={
            "error": {"code": code, "message": PRIVATE_MESSAGE, "param": PRIVATE_PROMPT},
            "debug_body": PRIVATE_BODY,
        },
    )


def assert_private_content_not_logged(log, public_error=None):
    recorded = json.dumps(log.tail(), ensure_ascii=False) + log.path.read_text(encoding="utf-8")
    if public_error is not None:
        recorded += str(public_error) + json.dumps(public_error.diagnostic_fields())
    for marker in (PRIVATE_MESSAGE, PRIVATE_BODY, PRIVATE_PROMPT, API_KEY):
        assert marker not in recorded


async def test_company_search_retries_only_same_query_after_temporary_429(config, event_log):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return limited_response()
        return httpx.Response(
            200,
            json={
                "search_results": [
                    {
                        "title": "合成公司官网",
                        "url": "https://company.example/about",
                        "snippet": "自研产品",
                        "chunks": [{"text": "直接招聘研发人员"}],
                    }
                ]
            },
        )

    search = CompanySearch(
        config(base_url="https://api.moonshot.cn/v1"), API_KEY, httpx.MockTransport(respond)
    )
    clock = FakeClock()
    search.recovery.bind(clock=clock)
    try:
        with event_log.bind():
            sources = await search.search({"name": "合成测试公司", "city": "北京"})
        assert len(requests) == 2
        assert [request.method for request in requests] == ["POST", "POST"]
        assert [request.url.path for request in requests] == ["/v1/tools/search_pro"] * 2
        assert requests[0].content == requests[1].content
        query = json.loads(requests[0].content)["text_query"]
        assert "合成测试公司 北京" in query
        assert len(sources) == 1
        assert sources[0]["text"] == "自研产品\n直接招聘研发人员"
        assert sources[0]["angle"] == "overview"
        assert search.allowed_urls == {"https://company.example/about"}
        assert clock.monotonic() == pytest.approx(0.25)
        waits = [entry for entry in event_log.tail() if entry["event"] == "ai.retry.wait"]
        assert len(waits) == 1
        assert waits[0]["fields"]["stage"] == "company_search_pro"
        assert waits[0]["fields"]["http_status"] == 429
        assert waits[0]["fields"]["wait_seconds"] == 0.25
        assert_private_content_not_logged(event_log)
    finally:
        await search.close()


async def test_company_quota_failure_makes_one_request_and_logs_only_safe_metadata(config, event_log):
    requests = []

    def respond(request):
        requests.append(request)
        return limited_response("insufficient_quota")

    search = CompanySearch(
        config(base_url="https://api.moonshot.cn/v1"), API_KEY, httpx.MockTransport(respond)
    )
    clock = FakeClock()
    search.recovery.bind(clock=clock)
    try:
        with event_log.bind(), pytest.raises(AiFailure) as caught:
            await search.search({"name": "合成测试公司", "city": "北京"})
        assert len(requests) == 1
        assert clock.monotonic() == 0
        assert search.allowed_urls == set()
        assert caught.value.code == "quota_exhausted"
        assert caught.value.is_limit and not caught.value.can_retry
        entries = [entry for entry in event_log.tail() if entry["event"] == "ai.request.limited"]
        assert len(entries) == 1
        fields = entries[0]["fields"]
        assert fields["stage"] == "company_search_pro"
        assert fields["http_status"] == 429
        assert fields["code"] == "quota_exhausted"
        assert fields["provider_code"] == "insufficient_quota"
        assert fields["limit_kind"] == "quota"
        assert not any(entry["event"] == "ai.retry.wait" for entry in event_log.tail())
        assert_private_content_not_logged(event_log, caught.value)
    finally:
        await search.close()


@pytest.mark.parametrize("stage", ["synthetic_request", "greeting"])
async def test_ai_service_retries_same_request_and_keeps_provider_body_out_of_logs(
    config, snapshot, job, event_log, stage
):
    requests = []
    greeting = {"text": "您好，我有 Python 服务开发经验，期待进一步交流。", "evidence": ["Python"]}

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return limited_response()
        content = json.dumps(greeting, ensure_ascii=False) if stage == "greeting" else "OK"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]
            },
        )

    ai = AiService(config(), API_KEY, httpx.MockTransport(respond))
    clock = FakeClock()
    ai.recovery.bind(clock=clock)
    try:
        with event_log.bind():
            if stage == "greeting":
                result = await ai.greet(snapshot(), job(), Assessment(80, "合成评分", (), (), ()))
                assert result.text == greeting["text"]
                assert result.evidence == ("Python",)
            else:
                result = await ai._request([{"role": "user", "content": PRIVATE_PROMPT}], stage=stage)
                assert result == "OK"
        assert len(requests) == 2
        assert requests[0].content == requests[1].content
        assert [request.url.path for request in requests] == ["/v1/chat/completions"] * 2
        if stage == "greeting":
            assert json.loads(requests[0].content)["response_format"] == {"type": "json_object"}
        assert clock.monotonic() == pytest.approx(0.25)
        waits = [entry for entry in event_log.tail() if entry["event"] == "ai.retry.wait"]
        assert len(waits) == 1
        assert waits[0]["fields"]["stage"] == stage
        assert waits[0]["fields"]["provider_code"] == "rate_limit_reached_error"
        assert waits[0]["fields"]["limit_kind"] == "temporary"
        assert_private_content_not_logged(event_log)
    finally:
        await ai.close()


async def test_ai_service_quota_failure_does_not_log_provider_message_or_body(config, event_log):
    requests = []

    def respond(request):
        requests.append(request)
        return limited_response("insufficient_quota")

    ai = AiService(config(), API_KEY, httpx.MockTransport(respond))
    clock = FakeClock()
    ai.recovery.bind(clock=clock)
    try:
        with event_log.bind(), pytest.raises(AiFailure) as caught:
            await ai._request([{"role": "user", "content": PRIVATE_PROMPT}], stage="synthetic_request")
        assert len(requests) == 1
        assert clock.monotonic() == 0
        assert caught.value.code == "quota_exhausted"
        assert caught.value.is_limit and not caught.value.can_retry
        entries = [entry for entry in event_log.tail() if entry["event"] == "ai.request.limited"]
        assert len(entries) == 1
        assert entries[0]["fields"]["stage"] == "synthetic_request"
        assert entries[0]["fields"]["http_status"] == 429
        assert entries[0]["fields"]["provider_code"] == "insufficient_quota"
        assert entries[0]["fields"]["limit_kind"] == "quota"
        assert_private_content_not_logged(event_log, caught.value)
    finally:
        await ai.close()
