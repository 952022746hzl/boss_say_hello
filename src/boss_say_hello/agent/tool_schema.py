def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
STRINGS = {"type": "array", "items": TEXT, "maxItems": 30}
SCORE = {"type": "integer", "minimum": 0, "maximum": 100}
ASSESSMENT = obj(dict(score=SCORE, reason=TEXT, evidence=STRINGS, gaps=STRINGS, missing=STRINGS))
BACKGROUND = obj(
    dict(
        identity_verified={"type": "boolean"},
        employment_checked={"type": "boolean"},
        outsourcing={"type": "string", "enum": ["clear", "suspected", "confirmed", "unknown"]},
        score=SCORE,
        reason=TEXT,
        source_ids=STRINGS,
    )
)
DEFINITIONS = {
    "open_preferences": (
        "打开固定的推荐页或简历期望页，随后读取控件；仅在条件设置阶段使用",
        dict(destination={"type": "string", "enum": ["recommendations", "expectations"]}),
    ),
    "inspect_preferences": ("读取期望职位和原生筛选控件；只能操作返回的控件 ID", {}),
    "preference_action": (
        "点击或填写已观察到的期望/筛选控件，返回真实变化；禁止盲目保存",
        dict(
            action={"type": "string", "enum": ["click", "input"]},
            option_id=TEXT,
            value={"type": ["string", "null"]},
        ),
    ),
    "verify_preferences": ("核对本轮目标职位、城市、原生筛选是否实际生效", {}),
    "list_jobs": ("读取下一批候选岗位卡片，达到候选上限或到底时返回 ended", {}),
    "read_job": ("读取已发现岗位的完整职责和公司线索", dict(job_id=TEXT)),
    "search_company": (
        "按公司身份联网查询，返回可引用来源；无证据不能断言无外包",
        dict(job_id=TEXT, angle={"type": "string", "enum": ["identity", "business", "employment", "risk"]}),
    ),
    "assess_job": (
        "根据真实岗位和已读取来源提交岗位分与背景判断，程序计算最终分并执行硬排除",
        dict(job_id=TEXT, assessment=ASSESSMENT, background=BACKGROUND),
    ),
    "draft_greeting": (
        "根据已确认简历、当前岗位和评分生成招呼语，预览模式在此结束岗位处理",
        dict(job_id=TEXT),
    ),
    "send_greeting": (
        "请求发送已有招呼语；发送开关及所有校验通过才会执行，结果未知不得重试",
        dict(job_id=TEXT),
    ),
    "skip_job": ("跳过已发现岗位并记录原因", dict(job_id=TEXT)),
    "finish": ("结束本轮并说明已完成事项、剩余事项或无法继续的原因", {}),
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": obj({"reason": TEXT, **properties}),
        },
    }
    for name, (description, properties) in DEFINITIONS.items()
]
