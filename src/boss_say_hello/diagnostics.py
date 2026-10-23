"""Local diagnostic events with an allowlist; payloads and exception text are excluded."""

import contextvars
import json
import logging
import threading
import traceback
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_current = contextvars.ContextVar("boss_diagnostics", default=None)
FIELDS = {
    "run_id",
    "job_id",
    "attempt_id",
    "stage",
    "status",
    "score",
    "count",
    "duration_ms",
    "read_timeout_seconds",
    "finish_reason",
    "json_error_position",
    "json_error_line",
    "json_error_column",
    "error_type",
    "cause_types",
    "http_status",
    "operation",
    "code",
    "char_count",
    "revision",
    "mode",
    "logged_in",
    "account_verified",
    "stack",
}
MESSAGES = {
    "app.ready": "后台服务已就绪",
    "app.closed": "后台服务已关闭",
    "operation.started": "后台操作开始",
    "operation.succeeded": "后台操作完成",
    "operation.failed": "后台操作失败",
    "operation.cancelled": "后台操作已取消",
    "ai.request.started": "模型请求开始",
    "ai.request.succeeded": "模型响应已收到",
    "ai.response.invalid_json": "模型响应 JSON 解析失败",
    "ai.request.failed": "模型请求失败",
    "browser.read.started": "读取浏览器页面",
    "browser.read.succeeded": "页面读取完成",
    "browser.read.failed": "页面读取失败",
    "delivery.intent": "发送步骤意图已落盘",
    "delivery.result": "发送步骤结果已落盘",
    "run.started": "本轮任务开始",
    "run.finished": "本轮任务结束",
    "run.job": "读取到岗位",
    "run.check": "岗位硬条件检查",
    "run.assessment": "匹配评分完成",
    "run.greeting": "招呼语草稿已生成",
    "run.delivery": "岗位发送处理结果",
    "run.state": "运行状态更新",
    "run.error": "岗位或运行发生错误",
    "session": "登录检测完成",
    "settings": "配置已加载或保存",
    "settings_saved": "配置已自动保存到本地工作目录",
    "resume": "简历版本已更新",
    "resume_draft": "本地简历编辑草稿已恢复",
    "resume_draft_saved": "关键词与经历摘要草稿已自动保存",
    "pause.requested": "用户请求暂停",
    "resume.requested": "用户请求继续",
    "app.unhandled": "捕获未处理异常",
    "history_runs": "运行历史已读取",
    "history_events": "运行详情已读取",
    "history.export_failed": "岗位 JSON 导出失败，完整记录仍保存在本地数据库",
    "platform": "平台能力检查完成",
    "journal.recovered": "已核对上次未完成的发送步骤",
    "delivery.reserved": "联系名额已预留",
    "delivery.failed": "发送步骤异常",
    "browser.return_failed": "返回列表失败",
    "resume.pdf.started": "开始提取 PDF",
    "resume.pdf.extracted": "PDF 文本已提取",
}


def record(event, **fields):
    logger = _current.get()
    if logger:
        logger.record(event, **fields)


def failure(event, exc, **fields):
    logger = _current.get()
    if logger:
        logger.failure(event, exc, **fields)


class EventLog:
    def __init__(self, root, sink=None, max_bytes=5 * 1024 * 1024, backups=5):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "application.log"
        self.sink = sink
        self.closed = False
        self.handler = RotatingFileHandler(
            self.path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger = logging.Logger(f"boss-local-{id(self)}", level=logging.DEBUG)
        self.logger.propagate = False
        self.logger.addHandler(self.handler)

    @contextmanager
    def bind(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def record(self, event, *, level="INFO", **fields):
        if self.closed:
            return
        fields = {k: v for k, v in fields.items() if k in FIELDS}
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "level": level,
            "event": event,
            "message": MESSAGES.get(event, event),
            "thread": threading.current_thread().name,
            "fields": fields,
        }
        self.logger.log(getattr(logging, level, logging.INFO), json.dumps(entry, ensure_ascii=False))
        if self.sink:
            self.sink(entry)

    def failure(self, event, exc, **fields):
        # Never format an exception: API errors may embed keys, URLs or request bodies.
        stack = [
            {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
            for f in traceback.extract_tb(exc.__traceback__)
        ]
        cause_types = []
        seen = {id(exc)}
        cause = exc.__cause__ or exc.__context__
        while cause is not None and id(cause) not in seen and len(cause_types) < 5:
            seen.add(id(cause))
            cause_types.append(type(cause).__name__)
            cause = cause.__cause__ or cause.__context__
        self.record(
            event,
            level="ERROR",
            error_type=type(exc).__name__,
            cause_types=cause_types,
            stack=stack,
            **fields,
        )

    def event(self, event):
        p = event.payload
        fields = {"run_id": event.run_id}
        for key in ("job_id", "status", "score", "mode"):
            if key in p:
                fields[key] = p[key]
        if event.kind == "session":
            fields.update(logged_in=p["session"].logged_in, account_verified=bool(p["session"].account_id))
        elif event.kind in ("settings", "settings_saved"):
            fields["revision"] = p["config"].revision
        elif event.kind == "resume":
            fields["char_count"] = len(p["version"].summary)
        elif event.kind == "greeting":
            fields["char_count"] = len(p["text"])
        elif event.kind == "finished":
            fields["count"] = p["contacts"]
        elif event.kind == "platform":
            fields["status"] = p["caps"].initial_contact_behavior
        if event.kind in ("notice", "ui_error", "log", "logs_ready", "credential"):
            return
        level = (
            "ERROR"
            if event.kind == "error"
            else "WARNING"
            if p.get("status") in ("unknown", "failed", "blocked", "pending", "needs_default_ack")
            else "INFO"
        )
        self.record(("run." if event.run_id else "") + event.kind, level=level, **fields)

    def tail(self, limit=300):
        self.handler.flush()
        with self.path.open(encoding="utf-8") as stream:
            lines = deque(stream, maxlen=limit)
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except (ValueError, TypeError):
                continue
        return result

    def close(self):
        if not self.closed:
            self.closed = True
            self.handler.close()
            self.logger.removeHandler(self.handler)
