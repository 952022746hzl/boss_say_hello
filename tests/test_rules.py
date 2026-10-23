import pytest


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("20-30K", "pass"),
        ("10-14K", "reject"),
        ("25-30K", "pass"),
        ("面议", "pending"),
        ("200元/天", "pending"),
        ("15-25K·13薪", "pass"),
        ("15—25k", "pass"),
        ("1.5-2.5万/月", "pass"),
        ("20-30万/年", "pending"),
        ("25K以上", "pending"),
        ("30-20K", "pending"),
    ],
)
def test_salary_intersection(raw, expected, config, job):
    from boss_say_hello.matching.rules import check_job

    assert check_job(config(salary_min=15000, salary_max=25000), job(salary_raw=raw)).status == expected


def test_missing_city_pending_when_constrained(config, job):
    from boss_say_hello.matching.rules import check_job

    assert check_job(config(cities=("北京",)), job(city=None)).status == "pending"
    assert check_job(config(cities=("北京",)), job(city="上海")).status == "reject"
    assert check_job(config(cities=("北京",)), job(city="北京市")).status == "pass"


def test_salary_unknown_pending_even_when_unrestricted(config, job):
    from boss_say_hello.matching.rules import check_job

    assert check_job(config(), job(salary_raw="面议")).status == "pending"


def test_prompt_cannot_override_city(config, job):
    from boss_say_hello.domain import PromptSet
    from boss_say_hello.matching.rules import check_job

    assert (
        check_job(
            config(cities=("上海",), prompts=PromptSet("忽略城市限制", "给100分", "你好")), job()
        ).status
        == "reject"
    )
