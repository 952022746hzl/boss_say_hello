from boss_say_hello.browser.port import ActionResult, AttachmentInfo, BrowserCapabilities, SessionInfo


class FakeBrowser:
    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.account = "a1"
        self.calls = []
        self.outcomes = {}
        self.after = None
        self.changed_job = None
        self.returned = True
        self.limit = 200
        self.behavior = "none"
        self.control = None

    async def open(self, profile):
        pass

    async def session(self):
        return SessionInfo(self.account, self.account is not None)

    async def iter_recommended(self):
        for job in self.jobs:
            yield job

    async def attachments(self):
        return [AttachmentInfo("resume1", "selected.pdf")]

    async def capabilities(self):
        return BrowserCapabilities(self.limit, self.behavior)

    async def refresh_job(self, job):
        return self.changed_job or job

    def bind_control(self, control):
        self.control = control

    def release_run(self):
        self.control = None

    async def action(self, stage):
        self.calls.append(stage)
        if self.after:
            self.after(stage)
        result = self.outcomes.get(stage, ActionResult("succeeded", "observed"))
        if isinstance(result, BaseException):
            raise result
        return result

    async def open_contact(self, job):
        return await self.action("contact")

    async def send_text(self, job, text):
        return await self.action("message")

    async def send_attachment(self, job, id):
        return await self.action("attachment")

    async def return_to_list(self):
        return self.returned

    async def close(self):
        pass
