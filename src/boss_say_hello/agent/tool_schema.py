import json


class SchemaValidationError(ValueError):
    """A failure described only by trusted schema metadata and fixed type names."""

    REASONS = {
        "type": "字段类型无效",
        "missing_field": "缺少必填字段",
        "unexpected_fields": "包含未知字段",
        "enum": "字段选项无效",
        "max_items": "列表过长",
        "min_length": "文本为空或过短",
        "max_length": "文本过长",
        "range": "数值超出范围",
    }

    def __init__(self, path, reason, expected, actual_type):
        self.path = path
        self.reason = reason
        self.expected = expected
        self.actual_type = actual_type
        super().__init__(f"{path}：{self.REASONS[reason]}；预期 {expected}，实际类型 {actual_type}")


_TYPES = {
    "string": str,
    "object": dict,
    "array": list,
    "integer": int,
    "boolean": bool,
    "null": type(None),
}
_TYPE_NAMES = {value: key for key, value in _TYPES.items()} | {float: "number"}


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_value(value, schema):
    """Validate the bounded schema subset used by our tools, including nested fields."""
    _validate_value(value, schema, "$")


def _validate_value(value, schema, path):
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    actual_type = _TYPE_NAMES.get(type(value), "unknown")
    if not any(type(value) is _TYPES[k] for k in kinds):
        raise SchemaValidationError(path, "type", " | ".join(kinds), actual_type)
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(path, "enum", json.dumps(schema["enum"], ensure_ascii=False), actual_type)
    if isinstance(value, dict):
        properties = schema["properties"]
        # Paths and expected fields come from the trusted schema, never response keys.
        for key, child in properties.items():
            if key not in value:
                child_kind = child["type"]
                expected = " | ".join(child_kind) if isinstance(child_kind, list) else child_kind
                raise SchemaValidationError(f"{path}.{key}", "missing_field", expected, "missing")
        if len(value) != len(properties):
            raise SchemaValidationError(
                path, "unexpected_fields", "object（仅允许字段：" + ", ".join(properties) + "）", actual_type
            )
        for key, child in properties.items():
            _validate_value(value[key], child, f"{path}.{key}")
    elif isinstance(value, list):
        maximum = schema.get("maxItems", 100)
        if len(value) > maximum:
            raise SchemaValidationError(path, "max_items", f"array（maxItems={maximum}）", actual_type)
        for index, child in enumerate(value):
            _validate_value(child, schema["items"], f"{path}[{index}]")
    elif isinstance(value, str):
        maximum, minimum = schema.get("maxLength", 2000), schema.get("minLength", 0)
        if len(value) > maximum:
            raise SchemaValidationError(path, "max_length", f"string（maxLength={maximum}）", actual_type)
        if len(value.strip()) < minimum:
            raise SchemaValidationError(
                path, "min_length", f"string（去除首尾空白后 minLength={minimum}）", actual_type
            )
    elif type(value) is int:
        if not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            bounds = ", ".join(f"{key}={schema[key]}" for key in ("minimum", "maximum") if key in schema)
            raise SchemaValidationError(path, "range", f"integer（{bounds}）", actual_type)


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
