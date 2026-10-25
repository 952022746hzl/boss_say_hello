from dataclasses import replace

import pytest


@pytest.mark.parametrize("enabled", [True, False])
def test_settings_restart_remembers_delivery_choice(tmp_path, enabled):
    from boss_say_hello.domain import AppConfig
    from boss_say_hello.storage.settings import ConfigStore

    store = ConfigStore(tmp_path)
    saved = store.save(replace(AppConfig(), auto_send=enabled))
    loaded = ConfigStore(tmp_path).load()
    assert loaded.auto_send is enabled
    assert loaded.revision == saved.revision


@pytest.mark.parametrize(
    "change",
    [
        {"salary_min": 20000, "salary_max": 10000},
        {"min_score": 101},
        {"max_contacts": 0},
        {"interval_seconds": 0},
        {"interval_seconds": float("nan")},
        {"max_contacts": True},
        {"model_timeout_seconds": 0},
        {"model_timeout_seconds": True},
        {"model_timeout_seconds": 1801},
    ],
)
def test_invalid_config_rejected(change):
    from boss_say_hello.domain import AppConfig

    with pytest.raises(ValueError):
        replace(AppConfig(), **change).validate()


def test_atomic_save_keeps_old_config_on_failure(tmp_path, monkeypatch):
    from boss_say_hello.domain import AppConfig
    from boss_say_hello.storage.settings import ConfigStore

    store = ConfigStore(tmp_path)
    store.save(AppConfig())
    old = (tmp_path / "settings.json").read_bytes()
    import os

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.save(replace(AppConfig(), cities=("北京",)))
    assert (tmp_path / "settings.json").read_bytes() == old


def test_model_timeout_persists_and_old_settings_receive_default(tmp_path):
    from boss_say_hello.domain import AppConfig
    from boss_say_hello.storage.settings import ConfigStore

    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    store = ConfigStore(tmp_path)
    assert store.load().model_timeout_seconds == 180
    store.save(replace(AppConfig(), model_timeout_seconds=240))
    assert ConfigStore(tmp_path).load().model_timeout_seconds == 240


def test_missing_auto_send_defaults_to_enabled(tmp_path):
    from boss_say_hello.storage.settings import ConfigStore

    store = ConfigStore(tmp_path)
    assert store.load().auto_send is True
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    assert store.load().auto_send is True
