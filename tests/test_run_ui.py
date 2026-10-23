from types import SimpleNamespace
from unittest.mock import Mock

from boss_say_hello.domain import RunEvent, public_dict


def test_unconfirmed_disables_start_and_results_visible(qtbot, job):
    from boss_say_hello.ui.run_page import RunPage

    c = SimpleNamespace(request_pause=Mock(), request_resume=Mock(), start_run=Mock())
    p = RunPage(c, lambda: None)
    qtbot.addWidget(p)
    assert not p.start_button.isEnabled()
    p.set_ready(True)
    assert p.start_button.isEnabled()
    p.on_event(RunEvent("r", "started", {"mode": "preview"}))
    p.on_event(RunEvent("r", "job", {"job_id": "j1", "job": public_dict(job())}))
    p.on_event(
        RunEvent(
            "r",
            "assessment",
            {
                "job_id": "j1",
                "score": 90,
                "reason": "Python 匹配",
                "evidence": ["项目"],
                "gaps": [],
                "missing": [],
            },
        )
    )
    p.on_event(RunEvent("r", "greeting", {"job_id": "j1", "text": "你好"}))
    p.table.selectRow(0)
    assert p.table.item(0, 4).text() == "90"
    assert "Python 匹配" in p.details.toPlainText() and "你好" in p.details.toPlainText()
    p.on_event(RunEvent("r", "finished", {"status": "limit_reached", "contacts": 2}))
    assert "2" in p.status.text() and p.start_button.isEnabled()
