import asyncio
import threading
from PyQt6.QtCore import QThread, pyqtSignal


class WorkerThread(QThread):
    event = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.ready = threading.Event()
        self.loop = None
        self.cleanups = []
        self._stopping = False

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def submit(self, coro):
        if not self.ready.wait(3) or self._stopping:
            coro.close()
            raise RuntimeError('后台服务尚未就绪或正在关闭')
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _shutdown(self):
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for cleanup in reversed(self.cleanups):
            try:
                await cleanup()
            except Exception:
                pass
        self.loop.call_soon(self.loop.stop)

    def request_shutdown(self):
        if self._stopping or not self.isRunning():
            return
        self.ready.wait(3)
        self._stopping = True
        asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
