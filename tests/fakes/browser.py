from boss_say_hello.browser.port import ActionResult, BrowserCapabilities, SessionInfo


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
        self.goal = None

    async def open(self, profile):
        pass

    async def session(self):
        return SessionInfo(self.account, self.account is not None)

    def bind_goal(self, config):
        self.goal = config

    async def preference_snapshot(self):
        return {
            "document": "synthetic-document",
            "expectations": [
                {"label": f"{self.goal.target_title}({self.goal.cities[0]})", "selected": True}
            ],
            "controls": [],
        }

    async def verify_preferences(self, receipt):
        return receipt == await self.preference_snapshot()

    async def candidate_batch(self):
        return {"jobs": self.jobs, "ended": True}

    def release_candidates(self, job_ids):
        pass

    async def current_candidate_ids(self):
        return {job.id for job in self.jobs}

    async def company_info(self, job):
        return {
            "id": job.company_id or "synthetic-company",
            "name": job.company,
            "city": job.city,
            "url": "https://company.example/about",
            "text": "自主研发产品，直接招聘",
        }

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

    async def return_to_list(self):
        return self.returned

    async def close(self):
        pass
