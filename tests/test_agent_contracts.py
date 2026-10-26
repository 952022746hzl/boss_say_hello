from dataclasses import replace

import pytest


def test_agent_config_persists_with_safe_defaults(tmp_path):
    from boss_say_hello.domain import AppConfig
    from boss_say_hello.storage.settings import ConfigStore

    store = ConfigStore(tmp_path)
    assert store.load().outsourcing_check is True
    store.save(replace(AppConfig(), outsourcing_check=False, background_weight=30, target_title="Java"))
    loaded = store.load()
    assert loaded.outsourcing_check is False and loaded.background_weight == 30
    assert loaded.target_title == "Java"


@pytest.mark.parametrize("change", [{"background_weight": True}, {"outsourcing_check": "false"}])
def test_agent_config_rejects_invalid_values(change):
    from boss_say_hello.domain import AppConfig

    with pytest.raises(ValueError):
        replace(AppConfig(), **change).validate()


def test_background_evidence_and_toggle(config, job):
    from boss_say_hello.agent.contracts import background_decision, combined_assessment
    from boss_say_hello.ai.schemas import AiFailure
    from boss_say_hello.domain import Assessment

    source = {"id": "e1", "url": "https://example.com/company", "text": "该公司提供驻场开发服务"}
    payload = {
        "identity_verified": True,
        "employment_checked": True,
        "outsourcing": "suspected",
        "score": 60,
        "reason": "有驻场业务",
        "source_ids": ["e1"],
    }
    result = background_decision(payload, [source], job(), config())
    assert result.status == "excluded"
    result = background_decision(payload, [source], job(), config(outsourcing_check=False))
    assert result.status == "pass"
    assessment = combined_assessment(Assessment(80, "相关经历", (), (), ()), result, config())
    assert assessment.score == 76
    with pytest.raises(AiFailure):
        background_decision({**payload, "source_ids": ["invented"]}, [source], job(), config())


def test_unknown_background_never_passes(config, job):
    from boss_say_hello.agent.contracts import background_decision

    payload = {
        "identity_verified": False,
        "employment_checked": False,
        "outsourcing": "unknown",
        "score": 50,
        "reason": "资料不足",
        "source_ids": [],
    }
    assert background_decision(payload, [], job(), config()).status == "pending"
    assert background_decision(payload, [], job(), config(outsourcing_check=False)).status == "pending"
