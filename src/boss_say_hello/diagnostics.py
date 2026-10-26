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

from boss_say_hello.agent.progress import PHASE_LABELS, TOOL_LABELS

_context = contextvars.ContextVar("boss_diagnostic_fields", default=None)
_current = contextvars.ContextVar("boss_diagnostics", default=None)
FIELDS = {
    "can_retry",
    "provider_code",
    "limit_kind",
    "retry_after_seconds",
    "wait_seconds",
    "retry",
    "ready",
    "text_matches",
    "button_enabled",
    "requires_default_ack",
    "actor",
    "control_id",
    "controls",
    "selected",
    "document",
    "path",
    "matched",
    "before_count",
    "model",
    "tool_count",
    "tool_names",
    "prompt_tokens",
    "completion_tokens",
    "result_keys",
    "poll",
    "clicked",
    "limit",
    "reason",
    "phase",
    "action",
    "control_label",
    "tool",
    "step",
    "message_count",
    "run_id",
    "job_id",
    "attempt_id",
    "stage",
    "status",
    "score",
    "count",
    "successes",
    "contacts",
    "contact_limit",
    "discovered",
    "handled",
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
    "ai.retry.started": "已重新发起请求，正在等待服务响应",
    "ai.retry.wait": "模型服务暂时限流，等待后重试",
    "ai.request.limited": "模型服务限制请求",
    "browser.editor.wait": "DEBUG｜等待发送控件就绪：核对会话、草稿内容和按钮状态",
    "browser.editor.refill": "输入框在页面加载后变空，重新填写本次草稿一次",
    "greeting.shorten.started": "招呼语超过长度上限，正在请求模型压缩一次",
    "greeting.shorten.succeeded": "招呼语压缩完成，长度校验通过",
    "agent.candidates.synced": "推荐列表已刷新，已核对并清理不再显示的旧候选岗位",
    "agent.job.unavailable": "岗位已不在当前推荐列表，已跳过并继续处理其他岗位",
    "ai.request.details": "DEBUG｜模型请求规模与工具数量",
    "browser.preference.snapshot": "DEBUG｜求职标签与可操作控件快照",
    "agent.tool.completed": "DEBUG｜工具执行完成",
    "ai.decision.details": "DEBUG｜模型决策返回与用量",
    "browser.delivery.started": "执行沟通操作",
    "browser.delivery.observed": "DEBUG｜核验目标会话与发送回执",
    "browser.delivery.completed": "沟通操作结果已核验",
    "browser.preference.started": "开始点击求职标签",
    "browser.preference.verify": "正在回读页面，核验标签操作结果",
    "browser.preference.completed": "求职标签控件操作已返回，页面状态已读取",
    "browser.preference.stale": "控件信息已变化，本次未操作，正在刷新控件",
    "run.cleaned": "本轮临时运行状态已清理，历史记录已保留",
    "run.blocked": "本轮启动条件未满足，未执行发送",
    "stop.requested": "用户请求结束本轮",
    "run.tool_action": "AI 请求执行工具",
    "run.tool_result": "工具执行结果已记录",
    "run.preferences": "本轮求职标签已核验",
    "run.company_sources": "公司查询来源已保存",
    "run.background": "公司背景结论已生成",
    "company.search.started": "公司联网查询开始",
    "company.search.succeeded": "公司联网查询完成",
    "company.search.failed": "公司联网查询失败",
    "agent.tool.failed": "代理工具执行失败",
    "agent.model.correction": "模型结果校验未通过，已限制纠正次数",
    "agent.run.failed": "代理运行中断",
    "browser.closed": "浏览器已退出，已暂停任务；点击打开 BOSS 登录可重新启动",
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
    "run.progress": "本轮发送进度",
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
    "resume.draft.cleanup_failed": "简历确认已保存，旧草稿文件暂未清理；旧草稿不会覆盖新版本",
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


@contextmanager
def scope(**fields):
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


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
        fields = {k: v for k, v in {**(_context.get() or {}), **fields}.items() if k in FIELDS}
        message = MESSAGES.get(event, event)
        if event == "ai.retry.wait":
            message = f"模型服务暂时限流，等待 {fields.get('wait_seconds', '?')} 秒后第 {fields.get('retry', '?')}/2 次重试"
        elif event == "ai.retry.started":
            message = f"已发起第 {fields.get('retry', '?')}/2 次重试，正在等待服务响应"
        elif event == "ai.request.limited":
            message += "：" + fields.get("reason", "请检查账户限制")
        elif event == "run.state" and fields.get("requires_default_ack"):
            message = "等待发送确认：" + fields.get("reason", "首次联系可能发送平台默认招呼语")
            message += "；请到「岗位与运行」点击「已知晓，继续自动发送」，或结束本轮关闭自动发送后预览"
        elif event == "run.state" and fields.get("status") == "paused":
            message = "已暂停：" + fields.get("reason", "请核对后点击继续本轮")
        elif event == "run.state" and fields.get("phase"):
            message = f"第 {fields.get('step', '?')} 步｜{PHASE_LABELS.get(fields['phase'], fields['phase'])}"
            if fields.get("tool"):
                message += "：" + TOOL_LABELS.get(fields["tool"], fields["tool"])
        elif event in {"run.progress", "run.finished"} and "successes" in fields:
            message = (fields.get("reason") or message) + (
                f" · 成功 {fields['successes']}/{fields['contact_limit']} 个岗位"
                f" · 尝试 {fields['contacts']} · 已发现 {fields.get('discovered', 0)} · 已处理 {fields.get('handled', 0)}"
            )
        elif event == "run.tool_action":
            message = (
                "程序执行：" if fields.get("actor") == "program" else "AI 决定执行："
            ) + TOOL_LABELS.get(fields.get("tool"), fields.get("tool", ""))
        elif event == "agent.tool.blocked":
            message = "本次操作未执行：" + fields.get("reason", "岗位条件尚未满足")
        elif event in {"agent.tool.failed", "agent.model.correction"} and fields.get("reason"):
            message += "：" + fields["reason"]
        elif event == "browser.preference.started":
            action = "点击"
            message = f"操作页面：{action}「{fields.get('control_label', '')}」"
        elif event == "browser.delivery.started":
            labels = {
                "contact": "点击立即沟通",
                "continue_contact": "点击继续沟通，等待聊天页",
                "fill_message": "填写招呼语",
                "send_message": "点击发送",
            }
            message = "操作页面：" + labels.get(fields.get("action"), fields.get("action", ""))
        elif event == "ai.request.started" and fields.get("stage") == "agent_decision":
            message = "已向模型请求下一步决策，正在等待模型响应"
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "level": level,
            "event": event,
            "message": message,
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
        for key in ("job_id", "status", "score", "mode", "tool", "step", "phase", "actor"):
            if key in p:
                fields[key] = p[key]
        if event.kind == "state":
            fields["reason"] = p.get("reason", "")
            fields["requires_default_ack"] = bool(p.get("requires_default_ack"))
        if event.kind == "session":
            fields.update(logged_in=p["session"].logged_in, account_verified=bool(p["session"].account_id))
        elif event.kind in ("settings", "settings_saved"):
            fields["revision"] = p["config"].revision
        elif event.kind == "resume":
            fields["char_count"] = len(p["version"].summary)
        elif event.kind == "greeting":
            fields["char_count"] = len(p["text"])
        elif event.kind in {"finished", "progress"}:
            for key in ("contacts", "successes", "contact_limit", "discovered", "handled", "reason"):
                if key in p:
                    fields[key] = p[key]
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
