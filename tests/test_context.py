from dataclasses import replace

import pytest


def test_three_prompts_have_separate_roles(snapshot, job):
    from boss_say_hello.ai.context import ContextBuilder
    from boss_say_hello.domain import Assessment, PromptSet

    run = snapshot()
    run = replace(run, config=replace(run.config, prompts=PromptSet("PREFER", "SCORE", "GREET")))
    scoring = str(ContextBuilder.assessment_messages(run, job()))
    greeting = str(ContextBuilder.greeting_messages(run, job(), Assessment(80, "reason", (), (), ())))
    assert "PREFER" in scoring and "SCORE" in scoring and "GREET" not in scoring
    assert "GREET" in greeting and "SCORE" not in greeting
    assert "salary_min" in scoring


def test_context_too_large_fails_without_truncation(snapshot, job):
    from boss_say_hello.ai.context import ContextBuilder
    from boss_say_hello.ai.schemas import AiFailure

    with pytest.raises(AiFailure):
        ContextBuilder.assessment_messages(snapshot(), job(text="x" * 50000))
