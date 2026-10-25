def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_value(value, schema):
    """Validate the bounded schema subset used by our tools, including nested fields."""
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    expected = {
        "string": str,
        "object": dict,
        "array": list,
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if not any(type(value) is expected[k] for k in kinds):
        raise ValueError("工具参数类型无效")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("工具参数选项无效")
    if isinstance(value, dict):
        if set(value) != set(schema["properties"]):
            raise ValueError("工具参数缺失或包含未知字段")
        for key, child in schema["properties"].items():
            validate_value(value[key], child)
    elif isinstance(value, list):
        if len(value) > schema.get("maxItems", 100):
            raise ValueError("工具参数列表过长")
        for child in value:
            validate_value(child, schema["items"])
    elif isinstance(value, str):
        if len(value) > schema.get("maxLength", 2000) or len(value.strip()) < schema.get("minLength", 0):
            raise ValueError("工具参数文本为空或过长")
    elif type(value) is int:
        if not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            raise ValueError("工具参数数值超出范围")


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
        "打开岗位推荐页，读取账号已有求职标签；仅在条件设置阶段使用",
        {},
    ),
    "inspect_preferences": ("读取已有求职标签控件；只能操作返回的控件 ID", {}),
    "preference_action": (
        "操作本次观察返回的控件 ID；只点击已有求职标签。不得猜测 ID 或用选项序号代替 ID；返回新快照后旧 ID 失效。不新增或编辑求职期望",
        dict(option_id=TEXT),
    ),
    "verify_preferences": ("核对目标职位和城市对应的求职标签是否实际生效", {}),
    "list_jobs": ("读取下一批候选岗位卡片，达到候选上限或到底时返回 ended", {}),
    "read_job": ("读取已发现岗位的完整职责和公司线索", dict(job_id=TEXT)),
    "search_company": (
        "每公司一次综合查询主体、业务和外包用工线索，返回可引用来源；无证据保持待确认，不重复查询",
        dict(job_id=TEXT),
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
