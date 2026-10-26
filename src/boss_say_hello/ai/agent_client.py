"""A single combined job/company evaluation, without browser-action decisions."""

import json
from dataclasses import asdict

from boss_say_hello.agent.tool_schema import (
    ASSESSMENT,
    BACKGROUND,
    SchemaValidationError,
    obj,
    validate_value,
)
from boss_say_hello.ai.client import AiService
from boss_say_hello.ai.prompts import BOUNDARY
from boss_say_hello.ai.schemas import AiFailure, decode

EVALUATION_SCHEMA = obj({"assessment": ASSESSMENT, "background": BACKGROUND})
EVALUATION_PROMPT = """仅根据当前岗位、已确认简历和本次提供的公司资料，完成一次综合评估。
岗位匹配分与公司背景分分别为0—100，综合分由程序计算。偏好和评分提示词仅用于评估，明确配置优先。
核对公司名称、城市、业务及平台主体线索，同名不能混同；当前岗位的用工性质须重新判断，不沿用缓存结论。
外包检查开启时，有资料支持的外包/驻场/派遣嫌疑标suspected，即使岗位声称直招；明确外包标confirmed。
没有搜到外包信息不等于clear；公司身份或岗位用工证据不足时相应布尔值为false，外包类别标unknown。
关闭外包检查只取消该项排除，仍核对主体和经营风险。背景source_ids只能引用本次提供的source_ids，不要填写网址、岗位ID或序号。
不执行或决定页面操作，不生成招呼语，不发送消息。reason仅写供用户查看的简短依据，不写内部推理。
以下给出的是校验用JSON Schema，不是要原样返回的结果。结果顶层只能有assessment和background两个字段。
两个对象内所有required字段都必须提供，不要添加其他字段。evidence、gaps、missing、source_ids必须是字符串数组；无内容时返回[]，不能省略或用null。
score必须写为0—100的整数，不要用引号、小数点或百分号；布尔字段只用true/false，不用字符串或0/1。
outsourcing只用小写clear、suspected、confirmed或unknown；reason必须是非空文字。
仅返回符合Schema的评估结果JSON，不要Markdown；收到校验反馈时先修正指出的字段约束，再返回完整结果。
"""


class AgentClient(AiService):
    async def evaluate_job(self, run, job, company, sources, *, correction=None):
        config = run.config
        context = {
            "明确配置": {
                "cities": config.cities,
                "salary_min": config.salary_min,
                "salary_max": config.salary_max,
                "outsourcing_check": config.outsourcing_check,
                "background_weight": config.background_weight,
            },
            "求职偏好": config.prompts.preference,
            "评分标准": config.prompts.scoring,
            "已确认简历": {"keywords": run.resume.keywords, "summary": run.resume.summary},
            "当前岗位": asdict(job),
            "公司主体线索": company,
            "公司资料": sources,
            "source_ids": sorted({s["id"] for s in sources if s.get("text") and s.get("url")}),
        }
        if correction:
            context["上次校验反馈"] = correction
        messages = [
            {
                "role": "system",
                "content": BOUNDARY + EVALUATION_PROMPT + json.dumps(EVALUATION_SCHEMA, ensure_ascii=False),
            },
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        result = decode(await self._request(messages, stage="assessment"))
        try:
            validate_value(result, EVALUATION_SCHEMA)
        except SchemaValidationError as exc:
            raise AiFailure("invalid_fields", "综合评估字段校验失败：" + str(exc)) from None
        except (ValueError, KeyError, TypeError):
            raise AiFailure("invalid_fields", "综合评估返回的结构或字段无效，请按给定结构重新评估") from None
        return result
