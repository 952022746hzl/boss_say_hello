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
# Only program-issued actions remain. Models return EVALUATION_SCHEMA data,
# never this action table or executable tool choices.
ACTION_SCHEMAS = {
    "inspect_preferences": {},
    "preference_action": dict(option_id=TEXT),
    "verify_preferences": {},
    "list_jobs": {},
    "read_job": dict(job_id=TEXT),
    "search_company": dict(job_id=TEXT),
    "assess_job": dict(job_id=TEXT, assessment=ASSESSMENT, background=BACKGROUND),
    "draft_greeting": dict(job_id=TEXT),
    "send_greeting": dict(job_id=TEXT),
    "skip_job": dict(job_id=TEXT),
}
