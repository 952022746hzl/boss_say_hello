import asyncio
import time


class Clock:
    def monotonic(self):
        return time.monotonic()

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


async def wait_until_ready(control, clock, deadline):
    while not control.closing.is_set():
        if not control.pause_requested.is_set() and clock.monotonic() >= deadline:
            return True
        await clock.sleep(0.1)
    return False
