import json
from dataclasses import asdict

from boss_say_hello.ai.prompts import BOUNDARY, GREETING_SCHEMA
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.greetings.salutation import salutation
from boss_say_hello.greetings.service import GREETING_MAX_LENGTH


def budget(messages):
    if len(json.dumps(messages, ensure_ascii=False)) > 48000:
        raise AiFailure("context_too_large", "上下文超过本地文本预算，请缩短并重新确认摘要或提示词")
    return messages


def facts(run, job):
    return {
        "明确配置": {
            "cities": run.config.cities,
            "salary_min": run.config.salary_min,
            "salary_max": run.config.salary_max,
        },
        "已确认简历": {"keywords": run.resume.keywords, "summary": run.resume.summary},
        "岗位资料": asdict(job),
    }


class ContextBuilder:
    @staticmethod
    def greeting_messages(run, job, assessment):
        return budget(
            [
                {
                    "role": "system",
                    "content": BOUNDARY
                    + GREETING_SCHEMA
                    + f"正文建议80—150字符，含称谓、标点、空格和英文字母在内，硬性上限{GREETING_MAX_LENGTH}字符；每个英文字母单独计数。优先保留最相关的1—2项经历，禁止堆砌全部技术栈。此长度约束优先于风格提示词。",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "招呼语提示词": run.config.prompts.greeting,
                            "招呼语开头": salutation(job.recruiter_name),
                            "称谓规则": "正文必须以指定开头起句；称谓取自当前招聘者页面，无明确称谓时用中性姓氏称呼，不猜性别。",
                            "资料": facts(run, job),
                            "匹配依据": assessment.evidence,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
