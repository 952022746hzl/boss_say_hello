from boss_say_hello.domain import CheckResult


def validate_greeting(greeting, max_length):
    text = greeting.text.strip()
    if not text or any(token in text.lower() for token in ('traceback','```','as an ai','api_key','<script')):
        return CheckResult('reject', ('招呼语为空或包含异常内容',))
    if max_length is None:
        return CheckResult('pending', ('平台消息长度限制尚未确认，仅预览',))
    if len(text) > max_length:
        return CheckResult('reject', ('超过已核实的平台消息长度限制',))
    return CheckResult('pass')


class GreetingService:
    def __init__(self, ai):
        self.ai = ai

    async def generate(self, run, job, assessment):
        if not run.resume.confirmed or assessment.score < run.config.min_score:
            raise ValueError('简历未确认或匹配分不足')
        return await self.ai.greet(run, job, assessment)
