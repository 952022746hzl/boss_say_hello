import json
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize(
    "name,expected",
    [
        ("王先生", "王先生您好"),
        ("张女士", "张女士您好"),
        ("李经理", "李经理您好"),
        ("王小明", "王老师您好"),
        ("欧阳明", "欧阳老师您好"),
        ("", "您好"),
        ("HR", "您好"),
    ],
)
def test_salutation_uses_page_name_or_neutral_address(name, expected):
    from boss_say_hello.greetings.salutation import salutation

    assert salutation(name) == expected


async def test_generated_greeting_gets_correct_prefix_without_another_model_call(config, snapshot, job):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.domain import Assessment

    ai = AiService(config(), "test-key")
    ai._request = AsyncMock(
        return_value=json.dumps(
            {"text": "您好，我有 Python 开发经验，希望进一步沟通。", "evidence": ["Python"]}
        )
    )
    try:
        result = await ai.greet(snapshot(), job(recruiter_name="张女士"), Assessment(80, "匹配", (), (), ()))
        assert result.text.startswith("张女士您好，")
        assert "您好，您好" not in result.text
        assert ai._request.await_count == 1
        context = ai._request.call_args.args[0]
        assert "张女士您好" in str(context)
    finally:
        await ai.close()
