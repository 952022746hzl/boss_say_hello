from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from boss_say_hello.domain import PromptSet


def test_prompt_roundtrip_and_switches_do_not_start(qtbot, config):
    from boss_say_hello.ui.settings_page import SettingsPage

    c = SimpleNamespace(
        save_settings=Mock(), test_connection=Mock(), save_credential=Mock(), use_credential=Mock()
    )
    p = SettingsPage(c)
    qtbot.addWidget(p)
    assert not p.auto_send.isChecked() and not p.send_attachment.isChecked()
    p.set_config(config(prompts=PromptSet("偏好A", "评分B", "风格C")))
    p.auto_send.setChecked(True)
    p.save()
    saved = c.save_settings.call_args.args[0]
    assert saved.prompts == PromptSet("偏好A", "评分B", "风格C")
    assert saved.auto_send
    c.test_connection.assert_not_called()


def test_invalid_salary_shows_field_error(qtbot):
    from boss_say_hello.ui.settings_page import SettingsPage

    c = SimpleNamespace(
        save_settings=Mock(), test_connection=Mock(), save_credential=Mock(), use_credential=Mock()
    )
    p = SettingsPage(c)
    qtbot.addWidget(p)
    p.salary_min.setText("30000")
    p.salary_max.setText("10000")
    p.save()
    c.save_settings.assert_not_called()
    assert "薪资" in p.status.text()


@pytest.mark.parametrize("action", ["save_key", "use_key"])
def test_credential_actions_keep_input_for_correction(qtbot, config, action):
    from PyQt6.QtWidgets import QLineEdit

    from boss_say_hello.ui.settings_page import SettingsPage

    c = SimpleNamespace(save_credential=Mock(), use_credential=Mock())
    page = SettingsPage(c)
    qtbot.addWidget(page)
    page.set_config(config())
    page.secret.setText("test-key")
    getattr(page, action)()
    assert page.secret.text() == "test-key"
    assert page.secret.echoMode() == QLineEdit.EchoMode.Password


def test_eye_button_toggles_key_visibility_without_changing_value(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLineEdit

    from boss_say_hello.ui.settings_page import SettingsPage

    page = SettingsPage(SimpleNamespace())
    qtbot.addWidget(page)
    page.secret.setText("test-key")
    assert page.secret.echoMode() == QLineEdit.EchoMode.Password
    qtbot.mouseClick(page.secret_visibility, Qt.MouseButton.LeftButton)
    assert page.secret.echoMode() == QLineEdit.EchoMode.Normal
    assert page.secret_visibility.accessibleName() == "隐藏密钥"
    qtbot.mouseClick(page.secret_visibility, Qt.MouseButton.LeftButton)
    assert page.secret.echoMode() == QLineEdit.EchoMode.Password
    assert page.secret_visibility.accessibleName() == "显示密钥"
    assert page.secret.text() == "test-key"


@pytest.mark.parametrize("action", ["save_key", "use_key", "test"])
def test_retained_key_requires_reentry_after_endpoint_change(qtbot, config, action):
    from boss_say_hello.ui.settings_page import SettingsPage

    c = SimpleNamespace(save_credential=Mock(), use_credential=Mock(), test_connection=Mock())
    page = SettingsPage(c)
    qtbot.addWidget(page)
    page.set_config(config())
    page.secret.setText("first-key")
    getattr(page, action)()
    method = {"save_key": c.save_credential, "use_key": c.use_credential, "test": c.test_connection}[action]
    assert method.call_count == 1
    page.base_url.setText("https://other.invalid/v1")
    getattr(page, action)()
    assert method.call_count == 1
    assert page.secret.text() == "first-key"
    assert page.secret.property("invalid") is True
    page.secret.setText("second-key")
    getattr(page, action)()
    assert method.call_count == 2
    assert method.call_args.args[1] == "second-key"


def test_never_used_key_cannot_follow_endpoint_change(qtbot, config):
    from boss_say_hello.ui.settings_page import SettingsPage

    c = SimpleNamespace(test_connection=Mock(), save_settings=Mock())
    page = SettingsPage(c)
    qtbot.addWidget(page)
    page.set_config(config(base_url="https://a.invalid/v1"))
    page.secret.setText("key-for-a")
    page.base_url.setText("https://b.invalid/v1")
    page.test()
    c.test_connection.assert_not_called()
    assert page.secret.property("invalid") is True


def test_saved_attachment_id_survives_restart_before_platform_discovery(qtbot, config):
    from boss_say_hello.browser.port import BrowserCapabilities
    from boss_say_hello.ui.settings_page import SettingsPage

    page = SettingsPage(SimpleNamespace(save_settings=Mock()))
    qtbot.addWidget(page)
    page.set_config(config(attachment_id="previous-attachment"))
    page.cities.setText("上海")
    assert page.read_config().attachment_id == "previous-attachment"
    page.set_platform(BrowserCapabilities(None, "unknown"), [])
    assert page.read_config().attachment_id == "previous-attachment"


def test_model_timeout_field_roundtrips_and_triggers_local_save(qtbot, config):
    from boss_say_hello.ui.settings_page import SettingsPage

    controller = SimpleNamespace(save_settings=Mock())
    page = SettingsPage(controller)
    qtbot.addWidget(page)
    page.set_config(config(model_timeout_seconds=240))
    assert page.model_timeout.value() == 240
    page.model_timeout.setValue(300)
    qtbot.waitUntil(lambda: controller.save_settings.called, timeout=2000)
    assert controller.save_settings.call_args.args[0].model_timeout_seconds == 300
