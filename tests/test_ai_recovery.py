import asyncio
import json
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from boss_say_hello.ai.schemas import AiFailure


class FakeClock:
    def __init__(self, on_sleep=None):
        self.now = 0
        self.sleeps = []
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()
        await asyncio.sleep(0)


def status_error(*, code=None, message="", headers=None, status=429, sdk=False):
    body = {"error": {"code": code, "message": message}}
    response = httpx.Response(
        status,
        json=body,
        headers=headers,
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )
    if sdk:
        return APIStatusError("private exception text", response=response, body=body)
    return httpx.HTTPStatusError("private exception text", request=response.request, response=response)


@pytest.mark.parametrize("sdk", [False, True])
async def test_temporary_limit_retries_same_operation_after_cooldown(sdk):
    from boss_say_hello.ai.recovery import RequestRecovery

    clock, waits = FakeClock(), []
    recovery = RequestRecovery(clock=clock, on_wait=lambda *args: waits.append(args))
    requests = 0

    async def operation():
        nonlocal requests
        requests += 1
        if requests == 1:
            raise status_error(code="rate_limit_reached_error", message="RPM limit reached", sdk=sdk)
        return {"complete": True}

    assert await recovery.call(operation, stage="agent_decision") == {"complete": True}
    assert requests == 2
    assert clock.now == pytest.approx(10)
    assert waits == [("agent_decision", 10, 1)]
    assert all(0 < seconds <= 0.1 for seconds in clock.sleeps)


@pytest.mark.parametrize(
    "code,message,expected_code,kind",
    [
        ("insufficient_quota", "", "quota_exhausted", "quota"),
        ("exceeded_current_quota_error", "", "quota_exhausted", "quota"),
        (None, "Insufficient balance", "quota_exhausted", "quota"),
        ("rate_limit_reached_error", "TPD limit reached", "daily_limit", "daily"),
        ("rate_limit_exceeded", "tokens per day exceeded", "daily_limit", "daily"),
        ("unknown-private-code", "private response content", "rate_limited", "unknown"),
    ],
)
async def test_nontransient_or_unknown_limits_never_retry(code, message, expected_code, kind):
    from boss_say_hello.ai.recovery import RequestRecovery

    requests = 0
    clock = FakeClock()
    raw = status_error(code=code, message=message, headers={"Retry-After": "1"} if kind != "unknown" else {})

    async def operation():
        nonlocal requests
        requests += 1
        raise raw

    with pytest.raises(AiFailure) as caught:
        await RequestRecovery(clock=clock).call(operation, stage="greeting")
    assert caught.value.code == expected_code
    assert caught.value.limit_kind == kind
    assert caught.value.is_limit
    assert not caught.value.can_retry
    assert caught.value.__cause__ is raw
    assert requests == 1 and not clock.sleeps


async def test_valid_retry_after_allows_unknown_429_and_honors_delay():
    from boss_say_hello.ai.recovery import RequestRecovery

    requests, clock = 0, FakeClock()

    async def operation():
        nonlocal requests
        requests += 1
        if requests == 1:
            raise status_error(headers={"Retry-After": "0.25"})
        return "ready"

    assert await RequestRecovery(clock=clock).call(operation, stage="company_search") == "ready"
    assert requests == 2 and clock.now == pytest.approx(0.25)


@pytest.mark.parametrize("header", ["61", "86400"])
async def test_long_retry_after_defers_to_outer_pause_without_request(header):
    from boss_say_hello.ai.recovery import RequestRecovery

    requests, clock = 0, FakeClock()

    async def operation():
        nonlocal requests
        requests += 1
        raise status_error(code="rate_limit_exceeded", headers={"Retry-After": header})

    with pytest.raises(AiFailure) as caught:
        await RequestRecovery(clock=clock).call(operation, stage="agent_decision")
    assert caught.value.retry_after_seconds == float(header)
    assert "超过自动等待上限" in caught.value.public_message
    assert header in caught.value.public_message
    assert requests == 1 and not clock.sleeps


async def test_temporary_limit_stops_after_three_requests():
    from boss_say_hello.ai.recovery import RequestRecovery

    requests, clock, waits = 0, FakeClock(), []

    async def operation():
        nonlocal requests
        requests += 1
        raise status_error(code="engine_overloaded_error")

    recovery = RequestRecovery(clock=clock, on_wait=lambda *args: waits.append(args))
    with pytest.raises(AiFailure) as caught:
        await recovery.call(operation, stage="agent_decision")
    assert caught.value.is_limit
    assert "自动重试 2 次仍未恢复" in caught.value.public_message
    assert caught.value.public_message == str(caught.value)
    assert requests == 3
    assert clock.now == pytest.approx(30)
    assert waits == [("agent_decision", 10, 1), ("agent_decision", 20, 2)]


@pytest.mark.parametrize("flag", ["pause_requested", "closing"])
async def test_run_control_interrupts_cooldown_without_another_request(flag, tmp_path):
    from boss_say_hello.ai.recovery import RequestRecovery
    from boss_say_hello.ai.schemas import AiRequestInterrupted
    from boss_say_hello.diagnostics import EventLog

    control = SimpleNamespace(pause_requested=Event(), closing=Event())
    clock = FakeClock(lambda: getattr(control, flag).set())
    requests, waits, retries = 0, [], []
    recovery = RequestRecovery()
    recovery.bind(control, clock, lambda *args: waits.append(args), lambda *args: retries.append(args))

    async def operation():
        nonlocal requests
        requests += 1
        raise status_error(code="rate_limit_exceeded")

    log = EventLog(tmp_path)
    try:
        with log.bind(), pytest.raises(AiRequestInterrupted):
            await recovery.call(operation, stage="greeting")
        assert not any(entry["event"] == "ai.retry.started" for entry in log.tail())
    finally:
        log.close()
    assert requests == 1
    assert clock.now <= 0.1
    assert len(waits) == 1
    assert not retries


async def test_retry_started_is_published_after_wait_before_each_repeated_request(tmp_path):
    from boss_say_hello.ai.recovery import RequestRecovery
    from boss_say_hello.diagnostics import EventLog

    clock, order = FakeClock(), []
    requests = 0

    async def operation():
        nonlocal requests
        requests += 1
        order.append(("request", requests))
        if requests < 3:
            raise status_error(code="rate_limit_exceeded", headers={"Retry-After": "0.25"})
        return "OK"

    recovery = RequestRecovery(
        clock=clock,
        on_retry=lambda stage, retry: order.append((stage, retry, clock.now)),
    )
    log = EventLog(tmp_path)
    try:
        with log.bind():
            assert await recovery.call(operation, stage="agent_decision") == "OK"
        events = [entry for entry in log.tail() if entry["event"].startswith("ai.retry.")]
        assert [entry["event"] for entry in events] == [
            "ai.retry.wait",
            "ai.retry.started",
            "ai.retry.wait",
            "ai.retry.started",
        ]
        starts = [entry["fields"] for entry in events if entry["event"] == "ai.retry.started"]
        assert [(fields["stage"], fields["retry"]) for fields in starts] == [
            ("agent_decision", 1),
            ("agent_decision", 2),
        ]
    finally:
        log.close()
    assert order == [
        ("request", 1),
        ("agent_decision", 1, 0.25),
        ("request", 2),
        ("agent_decision", 2, 0.5),
        ("request", 3),
    ]


async def test_wait_without_bound_control_remains_task_cancellable():
    from boss_say_hello.ai.recovery import RequestRecovery

    waiting = asyncio.Event()
    requests = 0

    async def operation():
        nonlocal requests
        requests += 1
        raise status_error(code="rate_limit_exceeded")

    task = asyncio.create_task(
        RequestRecovery(on_wait=lambda *_: waiting.set()).call(operation, stage="connection_test")
    )
    await asyncio.wait_for(waiting.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert requests == 1


async def test_wait_logs_without_callback_and_excludes_provider_content(tmp_path):
    from boss_say_hello.ai.recovery import RequestRecovery
    from boss_say_hello.diagnostics import EventLog

    secret = "private-key private-resume"
    requests = 0

    async def operation():
        nonlocal requests
        requests += 1
        if requests == 1:
            raise status_error(code=secret, message=secret, headers={"Retry-After": "0.25"})
        return "OK"

    log = EventLog(tmp_path)
    try:
        with log.bind():
            assert await RequestRecovery(clock=FakeClock()).call(operation, stage="connection_test") == "OK"
        waits = [entry for entry in log.tail() if entry["event"] == "ai.retry.wait"]
        assert len(waits) == 1
        assert waits[0]["level"] == "WARNING"
        assert waits[0]["fields"]["stage"] == "connection_test"
        assert waits[0]["fields"]["wait_seconds"] == 0.25
        assert waits[0]["fields"]["retry"] == 1
        assert secret not in json.dumps(log.tail())
        assert "provider_code" not in waits[0]["fields"]
    finally:
        log.close()


@pytest.mark.parametrize("header", ["NaN", "inf", "-1", "1e309", "private-key", ""])
def test_invalid_retry_after_never_enables_unknown_limit_retry(header):
    from boss_say_hello.ai.recovery import request_failure

    failure = request_failure(status_error(headers={"Retry-After": header}))
    assert failure.retry_after_seconds is None
    assert not failure.can_retry


def test_retry_after_http_date_is_parsed_using_utc(monkeypatch):
    from boss_say_hello.ai import recovery

    monkeypatch.setattr(recovery.time, "time", lambda: 1445412480)
    failure = recovery.request_failure(status_error(headers={"Retry-After": "Wed, 21 Oct 2015 07:28:05 GMT"}))
    assert failure.retry_after_seconds == 5
    assert failure.can_retry


def test_limit_diagnostics_drop_unknown_provider_fields_and_private_response():
    from boss_say_hello.ai.recovery import request_failure

    secret = "private-key private-resume"
    failure = request_failure(status_error(code=secret, message=secret, headers={"Retry-After": secret}))
    fields = failure.diagnostic_fields()
    assert fields["http_status"] == 429
    assert fields["code"] == "rate_limited"
    assert fields["limit_kind"] == "unknown"
    assert "provider_code" not in fields
    assert secret not in json.dumps(fields) + str(failure)
    unsafe = AiFailure(
        secret,
        http_status=secret,
        provider_code=secret,
        limit_kind=secret,
        retry_after_seconds=float("inf"),
    )
    assert secret not in json.dumps(unsafe.diagnostic_fields())
    assert "Infinity" not in json.dumps(unsafe.diagnostic_fields())


@pytest.mark.parametrize(
    "seconds",
    [float("nan"), float("inf"), -1, True, 10**1000],
    ids=["nan", "infinite", "negative", "boolean", "oversized-integer"],
)
def test_invalid_numeric_metadata_cannot_break_or_pollute_diagnostics(seconds):
    failure = AiFailure("rate_limited", http_status=429, retry_after_seconds=seconds)
    assert failure.retry_after_seconds is None
    assert "retry_after_seconds" not in failure.diagnostic_fields()


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "bad_request"),
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "not_found"),
        (500, "service_unavailable"),
    ],
)
def test_existing_http_error_mapping_is_preserved(status, code):
    from boss_say_hello.ai.recovery import request_failure

    failure = request_failure(status_error(status=status, message="private-key", sdk=True))
    assert failure.code == code and not failure.is_limit
    assert f"HTTP {status}" in failure.public_message
    assert "private-key" not in str(failure)


@pytest.mark.parametrize(
    "error_type,code", [(APITimeoutError, "request_timeout"), (APIConnectionError, "connection_failed")]
)
def test_existing_connection_error_mapping_is_preserved(error_type, code):
    from boss_say_hello.ai.recovery import request_failure

    failure = request_failure(error_type(request=httpx.Request("POST", "https://example.com")))
    assert failure.code == code and not failure.is_limit


async def test_nonlimit_error_is_left_to_existing_request_layer():
    from boss_say_hello.ai.recovery import RequestRecovery

    raw = status_error(status=401)

    async def operation():
        raise raw

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await RequestRecovery(clock=FakeClock()).call(operation, stage="agent_decision")
    assert caught.value is raw
