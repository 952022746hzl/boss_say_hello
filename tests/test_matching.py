from dataclasses import replace


async def test_rules_reject_before_ai(snapshot, job):
    from boss_say_hello.matching.service import MatchingService

    class AI:
        async def assess(self, *args):
            raise AssertionError("must not call AI")

    run = snapshot()
    run = replace(run, config=replace(run.config, cities=("上海",)))
    check, score = await MatchingService(AI()).evaluate(run, job())
    assert check.status == "reject" and score is None


async def test_no_keyword_count_gate(snapshot, job):
    from boss_say_hello.domain import Assessment
    from boss_say_hello.matching.service import MatchingService

    class AI:
        async def assess(self, *args):
            return Assessment(80, "related experience", (), (), ())

    check, score = await MatchingService(AI()).evaluate(
        snapshot(), job(text="A relevant role without exact keywords")
    )
    assert check.status == "pass" and score.score == 80


async def test_failed_score_never_returns_assessment(snapshot, job):
    import pytest

    from boss_say_hello.ai.schemas import AiFailure
    from boss_say_hello.matching.service import MatchingService

    class AI:
        async def assess(self, *args):
            raise AiFailure("timeout")

    with pytest.raises(AiFailure):
        await MatchingService(AI()).evaluate(snapshot(), job())
