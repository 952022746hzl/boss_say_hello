import json


def test_logs_rotate_and_exclude_private_payload(tmp_path):
    from boss_say_hello.diagnostics import EventLog

    entries = []
    log = EventLog(tmp_path, entries.append, max_bytes=600, backups=2)
    for _ in range(20):
        log.record(
            "job.read",
            job_id="synthetic-job",
            status="new",
            api_key="secret-key",
            resume_text="private-resume",
        )
    log.failure("ai.request", ValueError("secret-key private-resume"))
    log.close()
    files = list(tmp_path.glob("*.log*"))
    assert 2 <= len(files) <= 3
    text = "".join(p.read_text(encoding="utf-8") for p in files)
    assert "secret-key" not in text and "private-resume" not in text
    assert "ValueError" in text and entries[-1]["level"] == "ERROR"
    assert "thread" in entries[0] and "time" in entries[0]


async def test_model_request_logs_stage_and_duration_without_body(tmp_path, config):
    import httpx

    from boss_say_hello.ai.client import AiService
    from boss_say_hello.diagnostics import EventLog

    entries = []
    log = EventLog(tmp_path, entries.append)

    def response(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    with log.bind():
        ai = AiService(config(), "secret-key", transport=httpx.MockTransport(response))
        await ai.test_connection()
        await ai.close()
    log.close()
    assert [e["event"] for e in entries] == ["ai.request.started", "ai.request.succeeded"]
    assert entries[-1]["fields"]["duration_ms"] >= 0
    assert "secret-key" not in json.dumps(entries)


def test_log_page_filters_and_bounds_view(qtbot):
    from boss_say_hello.ui.log_page import LogPage

    page = LogPage()
    qtbot.addWidget(page)
    for level in ("INFO", "WARNING", "ERROR"):
        page.add_entry(
            {
                "time": "10:00",
                "level": level,
                "event": "test",
                "message": level,
                "fields": {},
                "thread": "test",
            }
        )
    page.level.setCurrentText("ERROR")
    assert "ERROR" in page.output.toPlainText() and "INFO" not in page.output.toPlainText()
    for _ in range(1100):
        page.add_entry(
            {
                "time": "10:00",
                "level": "INFO",
                "event": "test",
                "message": "test",
                "fields": {},
                "thread": "test",
            }
        )
    assert len(page.entries) <= 1000


def test_failure_logs_underlying_types_without_exception_messages(tmp_path):
    from boss_say_hello.diagnostics import EventLog

    log = EventLog(tmp_path)
    try:
        try:
            try:
                raise ValueError("private-key private-resume")
            except ValueError as exc:
                raise RuntimeError("private-key") from exc
        except RuntimeError as exc:
            log.failure("operation.failed", exc)
        entry = log.tail()[-1]
        assert entry["fields"]["cause_types"] == ["ValueError"]
        assert "private-key" not in json.dumps(entry)
        assert "private-resume" not in json.dumps(entry)
    finally:
        log.close()
