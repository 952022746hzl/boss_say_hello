"""Local full request bodies, with small metadata for each actual HTTP attempt."""

import asyncio
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from boss_say_hello.ai.recovery import request_failure
from boss_say_hello.ai.schemas import AiFailure, AiRequestInterrupted
from boss_say_hello.diagnostics import context_fields, current_log, record, scope

MAX_REQUEST_FILES = 500
MAX_REQUEST_BYTES = 100 * 1024 * 1024
_retention_lock = threading.RLock()
_active_paths = set()
_CREDENTIAL_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "proxy_authorization",
    "cookie",
    "set_cookie",
    "password",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "x_api_key",
}
_USAGE_FIELDS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "audio_tokens",
    "reasoning_tokens",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
}


def _redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {
            _redact(str(key), secret): _redact(item, secret)
            for key, item in value.items()
            if str(key).lower().replace("-", "_") not in _CREDENTIAL_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, secret) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Avoid repr(): an unsupported object may contain credentials or provider text.
    return "[unsupported parameter]"


def _request_body(payload, secret):
    body = dict(payload)
    options = body.pop("options", {})
    body.update(options)
    extra_body = body.pop("extra_body", None)
    for key in ("extra_headers", "extra_query", "timeout"):
        body.pop(key, None)
    if isinstance(extra_body, dict):
        body.update(extra_body)
    return _redact(body, secret)


def _endpoint(endpoint, secret):
    try:
        parts = urlsplit(endpoint)
        value = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
        return _redact(value, secret)
    except ValueError:
        return "[invalid endpoint]"


def _usage(value):
    if value is None:
        return {}
    result = {}
    for name in _USAGE_FIELDS:
        count = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if type(count) is int and count >= 0:
            result[name] = count
    for name in ("prompt_tokens_details", "completion_tokens_details"):
        details = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if details is not None:
            result[name] = _usage(details)
    return result


def _prune(directory):
    with _retention_lock:
        files = sorted(
            ((path.stat().st_mtime_ns, path.stat().st_size, path) for path in directory.glob("*.json")),
            key=lambda item: item[0],
        )
        total = sum(size for _, size, _ in files)
        count = len(files)
        for _, size, path in files:
            if count <= MAX_REQUEST_FILES and total <= MAX_REQUEST_BYTES:
                break
            if path in _active_paths:
                continue
            path.unlink()
            total -= size
            count -= 1


class RequestTrace:
    def __init__(self, stage, endpoint, payload, secret=""):
        self.request_id = uuid.uuid4().hex
        self.stage = stage
        self.endpoint = endpoint
        self.payload = payload
        self.secret = secret
        self.attempt = 0
        self.path = None
        self.body = None
        self._depth = 0
        self._warned = False

    @contextmanager
    def bind(self):
        self._depth += 1
        try:
            with scope(request_id=self.request_id, stage=self.stage):
                yield self
        finally:
            self._depth -= 1
            if self._depth == 0 and self.path is not None:
                with _retention_lock:
                    _active_paths.discard(self.path)
                try:
                    _prune(self.path.parent)
                except Exception:
                    self._warn()

    def _record(self, event, **fields):
        try:
            record(
                event,
                request_id=self.request_id,
                stage=self.stage,
                attempt=self.attempt,
                **({"request_path": str(self.path)} if self.path is not None else {}),
                **fields,
            )
        except Exception:
            # Logging callbacks and disk failures must never change an HTTP outcome.
            pass

    def _warn(self):
        if not self._warned:
            self._warned = True
            self._record("ai.request_log.unavailable", level="WARNING")

    def _prepare(self):
        logger = current_log()
        if logger is None:
            return
        try:
            if self.path is None:
                self.path = logger.root.resolve() / "ai_requests" / f"{self.request_id}.json"
            with _retention_lock:
                _active_paths.add(self.path)
            if self.body is None:
                context = context_fields()
                self.body = {
                    "request_id": self.request_id,
                    "stage": self.stage,
                    "endpoint": _endpoint(self.endpoint, self.secret),
                    "context": {
                        **{key: context[key] for key in ("run_id", "job_id", "step") if key in context},
                        "process_id": os.getpid(),
                    },
                    "request": _request_body(self.payload, self.secret),
                    "attempts": [],
                }
        except Exception:
            self._warn()

    def _persist(self):
        if current_log() is None or self.body is None or self.path is None:
            return
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.body, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            _prune(self.path.parent)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            self._warn()

    async def call(self, operation):
        with self.bind():
            self.attempt += 1
            self._prepare()
            attempt = {
                "attempt": self.attempt,
                "status": "started",
                "started_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            }
            if self.body is not None:
                self.body["attempts"].append(attempt)
            self._persist()
            self._record("ai.http.started", status="started")
            started = time.monotonic()
            try:
                result = await operation()
            except BaseException as exc:
                cancelled = isinstance(exc, (asyncio.CancelledError, AiRequestInterrupted))
                fields = {"status": "cancelled" if cancelled else "failed", "error_type": type(exc).__name__}
                try:
                    if not cancelled:
                        error = exc if isinstance(exc, AiFailure) else request_failure(exc)
                        fields.update(error.diagnostic_fields())
                except Exception:
                    pass
                fields["duration_ms"] = round((time.monotonic() - started) * 1000)
                attempt.update(fields)
                self._persist()
                self._record(
                    "ai.http.cancelled" if cancelled else "ai.http.failed",
                    level="WARNING" if cancelled else "ERROR",
                    **fields,
                )
                raise
            else:
                fields = {"status": "succeeded", "duration_ms": round((time.monotonic() - started) * 1000)}
                try:
                    status = getattr(result, "status_code", None)
                    if type(status) is int:
                        fields["http_status"] = status
                    usage = _usage(getattr(result, "usage", None))
                    if usage:
                        attempt["usage"] = usage
                        fields.update(
                            {
                                key: usage[key]
                                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                                if key in usage
                            }
                        )
                except Exception:
                    pass
                attempt.update(fields)
                self._persist()
                self._record("ai.http.succeeded", **fields)
                return result
