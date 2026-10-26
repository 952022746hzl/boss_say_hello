import pytest


@pytest.mark.parametrize(
    "text,limit,status",
    [
        ("", 200, "reject"),
        ("Traceback error", 200, "reject"),
        ("```json hello", 200, "reject"),
        ("你好，我做过相关项目。", 200, "pass"),
        ("你好" * 101, 200, "reject"),
        ("你好，有相关经验。", None, "pending"),
    ],
)
def test_greeting_validation(text, limit, status):
    from boss_say_hello.domain import Greeting
    from boss_say_hello.greetings.service import validate_greeting

    assert validate_greeting(Greeting(text), limit).status == status


async def test_low_score_does_not_generate(setup_agent):
    x = setup_agent()
    x.ai.evaluate_job.return_value["assessment"]["score"] = 20
    await x.engine.run()
    x.ai.greet.assert_not_called()
    assert not any(e.kind == "greeting" for e in x.events)
    assert not x.browser.calls
