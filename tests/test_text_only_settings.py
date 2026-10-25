from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PyQt6.QtWidgets import QCheckBox

from boss_say_hello.browser.port import BrowserCapabilities, SessionInfo
from boss_say_hello.domain import AppConfig, config_from_dict
from boss_say_hello.runtime.backend import Backend
from boss_say_hello.ui.settings_page import SettingsPage


def test_only_text_delivery_is_configurable(qtbot):
    page = SettingsPage(SimpleNamespace())
    qtbot.addWidget(page)
    assert not any("附件" in box.text() for box in page.findChildren(QCheckBox))
    assert not {"send_attachment", "attachment_id"} & asdict(page.read_config()).keys()


def test_retired_configuration_is_not_silently_supported():
    with pytest.raises(TypeError):
        config_from_dict({**asdict(AppConfig()), "send_attachment": True, "attachment_id": "retired"})


async def test_login_no_longer_reads_platform_attachments(tmp_path):
    events = []
    browser = SimpleNamespace(
        session=AsyncMock(return_value=SessionInfo("synthetic", True)),
        capabilities=AsyncMock(return_value=BrowserCapabilities(200, "may_default_message")),
        close=AsyncMock(),
    )
    backend = Backend(events.append, tmp_path, browser)
    backend.refresh_expectations = AsyncMock()
    try:
        await backend.initialize()
        await backend.check_login()
        platform = next(e for e in events if e.kind == "platform")
        assert set(platform.payload) == {"caps"}
        backend.refresh_expectations.assert_awaited_once()
    finally:
        await backend.close()
