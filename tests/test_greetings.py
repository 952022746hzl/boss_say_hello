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


async def test_low_score_does_not_generate(snapshot, job):
    from boss_say_hello.domain import Assessment
    from boss_say_hello.greetings.service import GreetingService

    class AI:
        async def greet(self, *args):
            raise AssertionError("must not call AI")

    with pytest.raises(ValueError):
        await GreetingService(AI()).generate(snapshot(), job(), Assessment(20, "low", (), (), ()))
