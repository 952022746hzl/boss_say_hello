from types import SimpleNamespace
from unittest.mock import Mock

from boss_say_hello.domain import RunEvent


def test_history_lists_runs_and_details(qtbot, snapshot):
    from boss_say_hello.ui.history_page import HistoryPage

    c = SimpleNamespace(load_history=Mock())
    p = HistoryPage(c)
    qtbot.addWidget(p)
    p.set_runs([snapshot()])
    p.runs.setCurrentRow(0)
    c.load_history.assert_called_with("r1")
    p.set_events(
        [
            RunEvent(
                "r1",
                "delivery",
                {"status": "unknown", "stages": {"message": {"outcome": "unknown", "detail": "待核实"}}},
            )
        ]
    )
    assert "待核实" in p.details.toPlainText()
