import json
from dataclasses import asdict

from boss_say_hello.ai.prompts import BOUNDARY, GREETING_SCHEMA, SCORE_SCHEMA
from boss_say_hello.ai.schemas import AiFailure


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
    def assessment_messages(run, job):
        return budget(
            [
                {"role": "system", "content": BOUNDARY + SCORE_SCHEMA},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "偏好提示词": run.config.prompts.preference,
                            "评分提示词": run.config.prompts.scoring,
                            "资料": facts(run, job),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )

    @staticmethod
    def greeting_messages(run, job, assessment):
        return budget(
            [
                {"role": "system", "content": BOUNDARY + GREETING_SCHEMA},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "招呼语提示词": run.config.prompts.greeting,
                            "资料": facts(run, job),
                            "匹配依据": assessment.evidence,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
