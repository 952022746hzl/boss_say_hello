import json

import httpx
import pytest


@pytest.mark.parametrize("value", [101, -1, True, 70.5, "70"])
def test_invalid_score_rejected(value):
    from boss_say_hello.ai.schemas import AiFailure, parse_assessment

    with pytest.raises(AiFailure):
        parse_assessment({"score": value, "reason": "r", "evidence": [], "gaps": [], "missing": []})


async def test_connection_request_has_no_resume(config):
    from boss_say_hello.ai.client import AiService

    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
                ],
            },
        )

    ai = AiService(config(), "secret-for-test", httpx.MockTransport(respond))
    await ai.test_connection()
    await ai.close()
    assert len(bodies) == 1
    assert bodies[0]["messages"] == [{"role": "user", "content": "Reply OK."}]
    assert "response_format" not in bodies[0]


@pytest.mark.parametrize("stage", ["assessment", "resume_extract", "greeting"])
async def test_structured_requests_enable_json_mode(stage, config, snapshot, job):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.domain import Assessment

    bodies = []
    payloads = {
        "assessment": {"score": 80, "reason": "相关经验", "evidence": [], "gaps": [], "missing": []},
        "resume_extract": {"keywords": ["Python"], "summary": "服务开发"},
        "greeting": {"text": "您好，期待沟通", "evidence": []},
    }

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payloads[stage])}, "finish_reason": "stop"}]
            },
        )

    ai = AiService(config(), "test-key", httpx.MockTransport(respond))
    try:
        if stage == "assessment":
            await ai.assess(snapshot(), job())
        elif stage == "resume_extract":
            await ai.extract_resume("Python 服务开发")
        else:
            await ai.greet(snapshot(), job(), Assessment(80, "相关经验", (), (), ()))
        assert bodies[0]["response_format"] == {"type": "json_object"}
    finally:
        await ai.close()


@pytest.mark.parametrize(
    "finish_reason,code", [("length", "response_truncated"), ("content_filter", "response_filtered")]
)
async def test_incomplete_response_cannot_return_a_score(finish_reason, code, config, snapshot, job):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.schemas import AiFailure

    payload = {"score": 99, "reason": "r", "evidence": [], "gaps": [], "missing": []}

    def respond(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": finish_reason}]},
        )

    ai = AiService(config(), "test-key", httpx.MockTransport(respond))
    try:
        with pytest.raises(AiFailure) as caught:
            await ai.assess(snapshot(), job())
        assert caught.value.code == code
    finally:
        await ai.close()


def test_json_parse_failure_logs_location_without_response_body(tmp_path):
    from boss_say_hello.ai.schemas import AiFailure, decode
    from boss_say_hello.diagnostics import EventLog

    log = EventLog(tmp_path)
    try:
        with log.bind(), pytest.raises(AiFailure) as caught:
            decode("private-resume private-key")
        assert caught.value.code == "invalid_json"
        entries = log.tail()
        assert entries[-1]["fields"]["json_error_position"] == 0
        assert "private" not in json.dumps(entries) + str(caught.value)
    finally:
        log.close()


@pytest.mark.parametrize("mode", ["timeout", "bad_json", "missing", "empty", "429"])
async def test_model_failures_never_return_score(mode, config, snapshot, job, caplog):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.schemas import AiFailure

    def respond(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-for-test", request=request)
        if mode == "429":
            return httpx.Response(429, json={"error": {"message": "secret-for-test"}})
        content = {"bad_json": "not json", "missing": '{"score":99}', "empty": ""}[mode]
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    ai = AiService(config(), "secret-for-test", httpx.MockTransport(respond))
    with pytest.raises(AiFailure) as caught:
        await ai.assess(snapshot(), job())
    assert "secret-for-test" not in str(caught.value) + caplog.text
    await ai.close()


async def test_fixed_sampling_model_uses_provider_defaults(config):
    from boss_say_hello.ai.client import AiService

    def respond(request):
        body = json.loads(request.content)
        if "temperature" in body or "top_p" in body:
            return httpx.Response(400, json={"error": {"message": "sampling is fixed"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    ai = AiService(config(model="kimi-k3"), "test-key", httpx.MockTransport(respond))
    try:
        await ai.test_connection()
    finally:
        await ai.close()


@pytest.mark.parametrize(
    "status, code",
    [
        (400, "bad_request"),
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "not_found"),
        (429, "rate_limited"),
        (500, "service_unavailable"),
    ],
)
async def test_http_failure_identifies_status_without_exposing_response(status, code, config, tmp_path):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.schemas import AiFailure
    from boss_say_hello.diagnostics import EventLog

    def respond(request):
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": "private-key private-resume",
                    "param": "private-key",
                    "code": "private-resume",
                }
            },
        )

    log = EventLog(tmp_path)
    ai = AiService(config(), "private-key", httpx.MockTransport(respond))
    try:
        with log.bind(), pytest.raises(AiFailure) as caught:
            await ai.test_connection()
        assert caught.value.code == code
        assert str(status) in caught.value.public_message
        entries = log.tail()
        assert entries[-1]["fields"]["http_status"] == status
        assert entries[-1]["fields"]["code"] == code
        assert "未进行发送" not in caught.value.public_message
        output = json.dumps(entries) + str(caught.value)
        assert "private-key" not in output and "private-resume" not in output
    finally:
        await ai.close()
        log.close()


@pytest.mark.parametrize("kind, code", [("timeout", "request_timeout"), ("connect", "connection_failed")])
async def test_network_failure_is_actionable(kind, code, config):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.schemas import AiFailure

    def respond(request):
        cls = httpx.ReadTimeout if kind == "timeout" else httpx.ConnectError
        raise cls("private-key", request=request)

    ai = AiService(config(), "private-key", httpx.MockTransport(respond))
    try:
        with pytest.raises(AiFailure) as caught:
            await ai.test_connection()
        assert caught.value.code == code
        assert "private-key" not in str(caught.value)
    finally:
        await ai.close()


async def test_pasted_key_outer_whitespace_is_removed_before_request(config):
    from boss_say_hello.ai.client import AiService

    headers = []

    def respond(request):
        headers.append(request.headers["authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    ai = AiService(config(), " \tprivate-key\r\n", httpx.MockTransport(respond))
    try:
        await ai.test_connection()
        assert headers == ["Bearer private-key"]
    finally:
        await ai.close()


@pytest.mark.parametrize("secret", ["key\nvalue", "key value", "key\tvalue", "key\x00", "key中文"])
def test_invalid_key_is_rejected_before_creating_client(config, secret):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.schemas import AiFailure

    with pytest.raises(AiFailure) as caught:
        AiService(config(), secret)
    assert caught.value.code == "invalid_key"
    assert secret not in str(caught.value)


@pytest.mark.parametrize("timeout", [None, 240])
async def test_model_read_timeout_uses_configuration_without_lengthening_connect(config, timeout):
    from dataclasses import replace

    from boss_say_hello.ai.client import AiService

    current = config()
    if timeout is not None:
        current = replace(current, model_timeout_seconds=timeout)
    observed = []

    def respond(request):
        observed.append(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    ai = AiService(current, "test-key", httpx.MockTransport(respond))
    try:
        await ai.test_connection()
        assert observed[0]["read"] == (180 if timeout is None else 240)
        assert observed[0]["connect"] == 15
    finally:
        await ai.close()
