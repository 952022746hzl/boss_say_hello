from dataclasses import replace

import pytest


def test_settings_restart_disables_delivery(tmp_path):
    from boss_say_hello.domain import AppConfig
    from boss_say_hello.storage.settings import ConfigStore

    store = ConfigStore(tmp_path)
    saved = store.save(replace(AppConfig(), auto_send=True, send_attachment=True, attachment_id="a"))
    loaded = ConfigStore(tmp_path).load()
    assert not loaded.auto_send and not loaded.send_attachment
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
