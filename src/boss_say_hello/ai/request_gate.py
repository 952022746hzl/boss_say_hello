"""Serialize logical AI requests, including their retries, on the worker loop."""

import asyncio
import time
from contextlib import asynccontextmanager

from boss_say_hello.ai.schemas import AiRequestInterrupted
from boss_say_hello.diagnostics import record


class RequestGate:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._queue_size = 0

    @staticmethod
    def _check_control(control):
        if control and (control.pause_requested.is_set() or control.closing.is_set()):
            raise AiRequestInterrupted("本轮已暂停或结束，取消等待模型请求")

    def _record(self, event, wait_ms):
        record(
            event,
            in_flight=int(self._lock.locked()),
            queue_size=self._queue_size,
            wait_ms=wait_ms,
        )

    @asynccontextmanager
    async def slot(self, control=None):
        self._check_control(control)
        started = time.monotonic()
        wait_ms = 0
        acquired = False
        self._queue_size += 1
        self._record("ai.queue.wait", wait_ms)
        try:
            try:
                while not acquired:
                    self._check_control(control)
                    try:
                        if control is None:
                            acquired = await self._lock.acquire()
                        else:
                            acquired = await asyncio.wait_for(self._lock.acquire(), timeout=0.05)
                    except TimeoutError:
                        continue
            finally:
                self._queue_size -= 1
            wait_ms = round((time.monotonic() - started) * 1000)
            self._record("ai.queue.acquired", wait_ms)
            self._check_control(control)
            yield
        finally:
            # A cancelled or interrupted waiter must never release another request's slot.
            if acquired:
                self._lock.release()
                self._record("ai.queue.released", wait_ms)
