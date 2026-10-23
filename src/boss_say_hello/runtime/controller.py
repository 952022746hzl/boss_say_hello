from PyQt6.QtCore import QObject, pyqtSignal

from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.browser.port import BrowserFailure
from boss_say_hello.domain import RunEvent
from boss_say_hello.runtime.backend import Backend
from boss_say_hello.runtime.control import RunControl
from boss_say_hello.storage.credentials import CredentialUnavailable


class Controller(QObject):
    event = pyqtSignal(object)

    def __init__(self, worker):
        super().__init__()
        self.worker = worker
        self.control = RunControl()
        self.backend = None
        self.current_resume = None
        self.resume_ready = False
        self.generation = 0
        self.run_pending = False
        worker.event.connect(self.receive)

    def receive(self, event):
        if event.kind == "resume":
            if event.payload["generation"] != self.generation:
                return
            self.current_resume = event.payload["version"]
            self.resume_ready = self.current_resume.confirmed
        if event.kind == "run_idle":
            self.run_pending = False
        self.event.emit(event)

    def submit(self, method, *args):
        async def call():
            try:
                if self.backend is None:
                    self.backend = Backend(self.worker.event.emit)
                    self.worker.cleanups.append(self.backend.close)
                await self.backend.initialize()
                await getattr(self.backend, method)(*args)
            except (ValueError, CredentialUnavailable, BrowserFailure) as exc:
                self.worker.event.emit(RunEvent("", "ui_error", {"message": str(exc)}))
            except AiFailure as exc:
                self.worker.event.emit(RunEvent("", "ui_error", {"message": exc.public_message}))
            except Exception:
                self.worker.event.emit(
                    RunEvent(
                        "",
                        "ui_error",
                        {"message": "操作失败，请检查配置、浏览器或本地文件；敏感错误详情不写入界面"},
                    )
                )
            finally:
                if method == "run":
                    self.worker.event.emit(RunEvent("", "run_idle", {}))

        return self.worker.submit(call())

    def load_settings(self):
        return self.submit("load_settings")

    def save_settings(self, config):
        return self.submit("save_settings", config)

    def test_connection(self, config, secret):
        return self.submit("test_connection", config, secret)

    def use_credential(self, id, secret):
        return self.submit("use_credential", id, secret)

    def save_credential(self, id, secret):
        return self.submit("save_credential", id, secret)

    def load_history(self, run_id=None):
        return self.submit("load_history", run_id)

    def open_login(self):
        return self.submit("open_login")

    def check_login(self):
        return self.submit("check_login")

    def invalidate_resume(self):
        self.generation += 1
        self.resume_ready = False
        self.event.emit(RunEvent("", "resume_dirty", {}))

    def import_resume(self, path):
        self.invalidate_resume()
        return self.submit("import_resume", path, self.generation)

    def confirm_resume(self, version, keywords, summary):
        self.invalidate_resume()
        return self.submit("confirm_resume", version, keywords, summary, self.generation)

    def start_run(self, config, resume=None):
        if self.run_pending:
            raise ValueError("已有任务正在运行或启动")
        if not self.resume_ready or self.current_resume is None:
            raise ValueError("请先核对并确认简历")
        config.validate(ready=True)
        self.control = RunControl()
        self.run_pending = True
        self.event.emit(RunEvent("", "run_pending", {}))
        return self.submit("run", config, self.current_resume, self.control)

    def request_pause(self):
        self.control.pause_requested.set()
        self.event.emit(
            RunEvent("", "notice", {"message": "已请求暂停；当前已发起的操作会先记录结果，后续步骤停止"})
        )

    def request_resume(self):
        self.control.pause_requested.clear()

    def close(self):
        self.control.closing.set()
        self.control.pause_requested.set()
        self.worker.request_shutdown()
