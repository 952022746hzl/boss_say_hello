from types import SimpleNamespace
from unittest.mock import Mock
from boss_say_hello.domain import PromptSet


def test_prompt_roundtrip_and_switches_do_not_start(qtbot,config):
    from boss_say_hello.ui.settings_page import SettingsPage
    c=SimpleNamespace(save_settings=Mock(),test_connection=Mock(),save_credential=Mock(),use_credential=Mock())
    p=SettingsPage(c); qtbot.addWidget(p)
    assert not p.auto_send.isChecked() and not p.send_attachment.isChecked()
    p.set_config(config(prompts=PromptSet('偏好A','评分B','风格C')))
    p.auto_send.setChecked(True)
    p.save()
    saved=c.save_settings.call_args.args[0]
    assert saved.prompts==PromptSet('偏好A','评分B','风格C')
    assert saved.auto_send
    c.test_connection.assert_not_called()


def test_invalid_salary_shows_field_error(qtbot):
    from boss_say_hello.ui.settings_page import SettingsPage
    c=SimpleNamespace(save_settings=Mock(),test_connection=Mock(),save_credential=Mock(),use_credential=Mock())
    p=SettingsPage(c); qtbot.addWidget(p)
    p.salary_min.setText('30000'); p.salary_max.setText('10000'); p.save()
    c.save_settings.assert_not_called()
    assert '薪资' in p.status.text()
