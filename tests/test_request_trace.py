import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from boss_say_hello.diagnostics import EventLog, record, scope


async def test_trace_persists_complete_request_and_sdk_usage_without_response(tmp_path):
    from boss_say_hello.ai.request_trace import RequestTrace

    entries = []
    log = EventLog(tmp_path, entries.append)
    messages = [{"role": "user", "content": "完整简历与岗位正文" * 2000}]
    options = {"temperature": 0.3, "tools": [{"type": "function", "function": {"name": "assess"}}]}
    result = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45, total_tokens=168),
        choices=[SimpleNamespace(message=SimpleNamespace(content="private response"), finish_reason="stop")],
    )

    async def operation():
        record("test.inside")
        return result

    with log.bind(), scope(run_id="r1", job_id="j1", step=3):
        trace = RequestTrace(
            "assessment",
            "https://model.invalid/v1/chat/completions",
            {
                "model": "test-model",
                "messages": messages,
                "options": options,
            },
        )
        with trace.bind():
            assert await trace.call(operation) is result
    log.close()

    paths = list((tmp_path / "ai_requests").glob("*.json"))
    assert len(paths) == 1
    body = json.loads(paths[0].read_text(encoding="utf-8"))
    assert body["request"] == {"model": "test-model", "messages": messages, **options}
    assert body["context"] == {"run_id": "r1", "job_id": "j1", "step": 3, "process_id": os.getpid()}
    assert body["stage"] == "assessment"
    attempt = body["attempts"][0]
    assert attempt["status"] == "succeeded" and attempt["attempt"] == 1
    assert attempt["duration_ms"] >= 0
    assert attempt["usage"] == {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}
    assert "private response" not in paths[0].read_text(encoding="utf-8")
    assert [entry["event"] for entry in entries] == ["ai.http.started", "test.inside", "ai.http.succeeded"]
    assert all(entry["fields"]["request_id"] == body["request_id"] for entry in entries)
    assert entries[-1]["fields"]["request_path"] == str(paths[0])
    assert "完整简历" not in (tmp_path / "application.log").read_text(encoding="utf-8")


async def test_retry_keeps_request_id_and_records_sanitized_limit_metadata(tmp_path):
    from boss_say_hello.ai.request_trace import RequestTrace

    log = EventLog(tmp_path)
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://model.invalid"),
        json={
            "error": {"code": "rate_limit_exceeded", "message": "private-error-body"},
        },
        headers={"retry-after": "2"},
    )
    error = httpx.HTTPStatusError("private-error-body", request=response.request, response=response)

    async def rejected():
        raise error

    async def accepted():
        return httpx.Response(200, json={"private": "response"})

    with log.bind():
        trace = RequestTrace("assessment", "https://model.invalid/v1", {"model": "m"})
        with trace.bind():
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await trace.call(rejected)
            assert caught.value is error
            await trace.call(accepted)
    entries = log.tail()
    log.close()
    files = list((tmp_path / "ai_requests").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert [a["attempt"] for a in body["attempts"]] == [1, 2]
    assert [a["status"] for a in body["attempts"]] == ["failed", "succeeded"]
    assert body["attempts"][0]["http_status"] == 429
    assert body["attempts"][0]["limit_kind"] == "temporary"
    assert body["attempts"][0]["provider_code"] == "rate_limit_exceeded"
    assert body["attempts"][1]["http_status"] == 200
    assert len({entry["fields"]["request_id"] for entry in entries}) == 1
    assert "private-error-body" not in json.dumps(body)
    assert "private-error-body" not in json.dumps(entries)


async def test_credentials_are_removed_from_body_and_endpoint(tmp_path):
    from boss_say_hello.ai.request_trace import RequestTrace

    log = EventLog(tmp_path)
    with log.bind():
        trace = RequestTrace(
            "assessment",
            "https://username:password@model.invalid/v1?key=query-secret#fragment-secret",
            {
                "model": "m",
                "messages": [{"role": "user", "content": "prefix key-secret suffix"}],
                "options": {
                    "api_key": "body-key",
                    "headers": {"Authorization": "auth-secret", "Cookie": "cookie-secret"},
                    "extra_body": {"reasoning_effort": "high"},
                },
            },
            secret="key-secret",
        )
        with trace.bind():
            await trace.call(lambda: asyncio.sleep(0, result="ok"))
    log.close()
    text = next((tmp_path / "ai_requests").glob("*.json")).read_text(encoding="utf-8")
    for secret in (
        "key-secret",
        "body-key",
        "auth-secret",
        "cookie-secret",
        "username",
        "password",
        "query-secret",
        "fragment-secret",
    ):
        assert secret not in text
    body = json.loads(text)
    assert body["endpoint"] == "https://model.invalid/v1"
    assert body["request"]["reasoning_effort"] == "high"
    assert body["request"]["messages"][0]["content"] == "prefix [REDACTED] suffix"


async def test_no_bound_event_log_does_not_write_request_files(tmp_path, monkeypatch):
    from boss_say_hello.ai.request_trace import RequestTrace

    monkeypatch.chdir(tmp_path)
    trace = RequestTrace("connection_test", "https://model.invalid", {"model": "m"})
    with trace.bind():
        assert await trace.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
    assert list(tmp_path.iterdir()) == []


async def test_logging_failure_keeps_result_and_exception_and_emits_fixed_warning(tmp_path):
    from boss_say_hello.ai.request_trace import RequestTrace

    log = EventLog(tmp_path)
    (tmp_path / "ai_requests").write_text("blocked", encoding="utf-8")
    error = RuntimeError("private operation error")

    async def rejected():
        raise error

    with log.bind():
        trace = RequestTrace("assessment", "https://model.invalid", {"model": "m"})
        with trace.bind():
            assert await trace.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
            with pytest.raises(RuntimeError) as caught:
                await trace.call(rejected)
            assert caught.value is error
    entries = log.tail()
    log.close()
    warning = next(entry for entry in entries if entry["event"] == "ai.request_log.unavailable")
    assert warning["level"] == "WARNING"
    assert "完整请求日志无法写入" in warning["message"]
    assert "private operation error" not in json.dumps(entries)


async def test_sink_error_cannot_replace_success_or_cancellation(tmp_path):
    from boss_say_hello.ai.request_trace import RequestTrace

    def broken_sink(entry):
        raise RuntimeError("private sink error")

    log = EventLog(tmp_path, broken_sink)
    error = asyncio.CancelledError()

    async def cancelled():
        raise error

    with log.bind():
        trace = RequestTrace("assessment", "https://model.invalid", {"model": "m"})
        with trace.bind():
            assert await trace.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
            with pytest.raises(asyncio.CancelledError) as caught:
                await trace.call(cancelled)
            assert caught.value is error
    entries = log.tail()
    log.close()
    assert entries[-1]["event"] == "ai.http.cancelled"
    body = json.loads(next((tmp_path / "ai_requests").glob("*.json")).read_text(encoding="utf-8"))
    assert body["attempts"][-1]["status"] == "cancelled"


async def test_retention_removes_old_completed_requests_but_keeps_active_request(tmp_path, monkeypatch):
    from boss_say_hello.ai import request_trace

    monkeypatch.setattr(request_trace, "MAX_REQUEST_FILES", 2)
    log = EventLog(tmp_path)
    with log.bind():
        active = request_trace.RequestTrace("assessment", "https://model.invalid", {"model": "active"})
        with active.bind():
            await active.call(lambda: asyncio.sleep(0))
            active_path = next((tmp_path / "ai_requests").glob("*.json"))
            for number in range(4):
                trace = request_trace.RequestTrace(
                    "greeting", "https://model.invalid", {"model": str(number)}
                )
                with trace.bind():
                    await trace.call(lambda: asyncio.sleep(0))
            assert active_path.exists()
            assert len(list((tmp_path / "ai_requests").glob("*.json"))) <= 2
    log.close()
