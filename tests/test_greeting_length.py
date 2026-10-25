import json
from unittest.mock import AsyncMock

import pytest

from boss_say_hello.ai.client import AiService
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.domain import Assessment, Greeting
from boss_say_hello.runtime.control import RunControl
from tests.test_delivery import setup


async def test_overlong_greeting_is_shortened_once_after_address_is_added(config, snapshot, job):
    ai = AiService(config(), "test-key")
    ai._request = AsyncMock(
        side_effect=[
            json.dumps({"text": "您好，" + "开发经验" * 49, "evidence": ["开发经验"]}),
            json.dumps({"text": "您好，我有相关开发经验，希望进一步沟通。", "evidence": ["开发经验"]}),
        ]
    )
    try:
        result = await ai.greet(snapshot(), job(recruiter_name="钟先生"), Assessment(80, "匹配", (), (), ()))
        assert result.text.startswith("钟先生您好，") and len(result.text) <= 200
        assert ai._request.await_count == 2
        assert ai._request.call_args.kwargs["stage"] == "greeting_shorten"
        assert "200" in str(ai._request.call_args_list[0].args[0])
    finally:
        await ai.close()


async def test_still_overlong_after_one_shortening_fails_without_unbounded_calls(config, snapshot, job):
    ai = AiService(config(), "test-key")
    ai._request = AsyncMock(return_value=json.dumps({"text": "开发经验" * 60, "evidence": ["开发经验"]}))
    try:
        with pytest.raises(AiFailure) as caught:
            await ai.greet(snapshot(), job(), Assessment(80, "匹配", (), (), ()))
        assert caught.value.code == "greeting_too_long"
        assert ai._request.await_count == 2
    finally:
        await ai.close()


async def test_length_failure_explains_actual_length_without_contact(snapshot, config, job, storage):
    browser, journal, service = setup(storage)
    result = await service.deliver(
        snapshot(config=config(auto_send=True)),
        job(),
        Assessment(80, "匹配", (), (), ()),
        Greeting("字" * 206),
        RunControl(),
    )
    reason = result[0].payload["reason"]
    assert "206" in reason and "200" in reason and "超" in reason
    assert "未核实" not in reason
    assert browser.calls == [] and journal.contact_count("r1") == 0


async def test_unknown_behavior_has_separate_reason(snapshot, config, job, storage):
    browser, _, service = setup(storage)
    browser.behavior = "unknown"
    result = await service.deliver(
        snapshot(config=config(auto_send=True)),
        job(),
        Assessment(80, "匹配", (), (), ()),
        Greeting("您好"),
        RunControl(),
    )
    assert "沟通行为" in result[0].payload["reason"] and "长度" not in result[0].payload["reason"]
    assert browser.calls == []


def test_length_validation_matches_browser_utf16_units():
    from boss_say_hello.greetings.service import validate_greeting

    assert validate_greeting(Greeting("字" * 199 + "😀"), 200).status == "reject"
