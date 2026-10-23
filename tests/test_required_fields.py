from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QLabel


def test_model_fields_have_red_required_labels_and_invalid_border(qtbot):
    from boss_say_hello.ui.settings_page import SettingsPage

    c = SimpleNamespace(
        save_settings=Mock(), test_connection=Mock(), save_credential=Mock(), use_credential=Mock()
    )
    page = SettingsPage(c)
    qtbot.addWidget(page)
    labels = [x.text() for x in page.findChildren(QLabel)]
    assert any("API 地址" in s and "*" in s and "#c62828" in s for s in labels)
    assert any("模型名称" in s and "*" in s and "#c62828" in s for s in labels)
    page.test()
    assert page.base_url.property("invalid") is True and page.model.property("invalid") is True
    c.test_connection.assert_not_called()
    page.base_url.setText("https://model.invalid/v1")
    page.model.setText("model")
    assert not page.base_url.property("invalid") and not page.model.property("invalid")


def test_resume_required_fields_mark_empty_on_confirm(qtbot, resume):
    from boss_say_hello.ui.resume_page import ResumePage

    c = SimpleNamespace(invalidate_resume=Mock(), confirm_resume=Mock(), import_resume=Mock())
    page = ResumePage(c)
    qtbot.addWidget(page)
    page.set_version(resume())
    page.summary.clear()
    page.confirm()
    assert page.summary.property("invalid") is True
    c.confirm_resume.assert_not_called()
