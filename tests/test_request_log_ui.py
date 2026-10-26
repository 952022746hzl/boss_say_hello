import asyncio
import json

from boss_say_hello.diagnostics import EventLog
from boss_say_hello.ui.log_page import LogPage


async def test_request_detail_reopens_full_body_and_selection_is_separate_from_progress(qtbot, tmp_path):
    from boss_say_hello.ai.request_trace import RequestTrace

    log = EventLog(tmp_path)
    with log.bind():
        trace = RequestTrace(
            "assessment",
            "https://model.invalid",
            {
                "model": "m",
                "messages": [{"role": "user", "content": f"正文第{i}段"} for i in range(1200)],
            },
        )
        with trace.bind():
            await trace.call(lambda: asyncio.sleep(0))
        second = RequestTrace("greeting", "https://model.invalid", {"model": "m", "messages": []})
        with second.bind():
            await second.call(lambda: asyncio.sleep(0))
    log.close()
    entries = [
        json.loads(line) for line in (tmp_path / "application.log").read_text(encoding="utf-8").splitlines()
    ]
    page = LogPage()
    qtbot.addWidget(page)
    page.set_location(tmp_path, entries)
    assert page.request_selector.count() == 2
    page.request_selector.setCurrentIndex(0)
    text = page.request_details.toPlainText()
    assert "正文第0段" in text and "正文第1199段" in text
    assert page.request_details.document().blockCount() > 1000
    assert page.request_details.maximumBlockCount() == 0
    assert "ai_requests" in page.request_location.text()
    assert "正文第0段" not in page.output.toPlainText()
    page.request_selector.setCurrentIndex(1)
    assert json.loads(page.request_details.toPlainText())["stage"] == "greeting"
    page.clear_view()
    assert page.request_selector.count() == 0 and not page.request_details.toPlainText()


def test_missing_request_body_shows_fixed_help_without_crashing(qtbot, tmp_path):
    page = LogPage()
    qtbot.addWidget(page)
    page.set_location(
        tmp_path,
        [
            {
                "time": "10:00",
                "event": "ai.http.succeeded",
                "level": "INFO",
                "message": "请求完成",
                "fields": {
                    "request_id": "missing",
                    "stage": "assessment",
                    "request_path": str(tmp_path / "missing.json"),
                },
            }
        ],
    )
    assert "无法读取完整请求日志" in page.request_details.toPlainText()
    assert str(tmp_path / "missing.json") in page.request_location.text()


def test_queue_events_preserve_concurrency_and_wait_metadata(qtbot, tmp_path):
    page = LogPage()
    qtbot.addWidget(page)
    log = EventLog(tmp_path, page.add_entry)
    try:
        for event in ("ai.queue.wait", "ai.queue.acquired", "ai.queue.released"):
            log.record(event, request_id="request-1", in_flight=1, queue_size=2, wait_ms=25)
        entries = log.tail()
        assert all(
            e["fields"] == {"request_id": "request-1", "in_flight": 1, "queue_size": 2, "wait_ms": 25}
            for e in entries
        )
        assert "排队" in page.output.toPlainText()
        assert "请求槽" in page.output.toPlainText()
    finally:
        log.close()
