from boss_say_hello.matching.rules import check_job


class MatchingService:
    def __init__(self, ai):
        self.ai = ai

    async def evaluate(self, run, job):
        if not run.resume.confirmed:
            raise ValueError('简历尚未确认')
        check = check_job(run.config, job)
        if check.status != 'pass':
            return check, None
        return check, await self.ai.assess(run, job)
