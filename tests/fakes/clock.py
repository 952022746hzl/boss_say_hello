import asyncio


class FakeClock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)
