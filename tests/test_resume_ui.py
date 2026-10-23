from types import SimpleNamespace


def test_edit_invalidates_ui_confirmation(qtbot, resume):
    from boss_say_hello.ui.resume_page import ResumePage

    calls = []
    controller = SimpleNamespace(
        invalidate_resume=lambda: calls.append("dirty"),
        confirm_resume=lambda *args: calls.append(args),
        import_resume=lambda p: None,
    )
    page = ResumePage(controller)
    qtbot.addWidget(page)
    page.set_version(resume())
    assert "已确认" in page.status.text()
    page.summary.setPlainText("edited")
    assert "未确认" in page.status.text() and calls == ["dirty"]
    page.confirm_button.click()
    assert calls[-1][2] == "edited"
