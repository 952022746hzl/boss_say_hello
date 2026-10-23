import json

from boss_say_hello.diagnostics import record
from boss_say_hello.domain import Assessment, Greeting


class AiFailure(RuntimeError):
    def __init__(self, code: str, public_message: str = "模型返回内容无效，请检查配置或重新评估"):
        self.code = code
        self.public_message = public_message
        super().__init__(public_message)


def decode(content: str) -> dict:
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        record(
            "ai.response.invalid_json",
            level="ERROR",
            code="invalid_json",
            char_count=len(content),
            json_error_position=exc.pos,
            json_error_line=exc.lineno,
            json_error_column=exc.colno,
        )
        raise AiFailure(
            "invalid_json", "模型已返回，但内容不是有效 JSON，本次结果不可用，请重新评估"
        ) from None
    except (ValueError, TypeError):
        raise AiFailure("invalid_json") from None
    if not isinstance(result, dict):
        raise AiFailure("invalid_json")
    return result


def strings(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
        raise AiFailure("invalid_fields")
    return tuple(s.strip() for s in value)


def text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AiFailure("empty_content")
    return value.strip()


def parse_assessment(payload: dict) -> Assessment:
    try:
        score = payload["score"]
        if type(score) is not int or not 0 <= score <= 100:
            raise AiFailure("invalid_score")
        return Assessment(
            score,
            text(payload["reason"]),
            strings(payload["evidence"]),
            strings(payload["gaps"]),
            strings(payload["missing"]),
        )
    except (KeyError, TypeError):
        raise AiFailure("invalid_fields") from None


def parse_greeting(payload: dict) -> Greeting:
    try:
        return Greeting(text(payload["text"]), strings(payload["evidence"]))
    except (KeyError, TypeError):
        raise AiFailure("invalid_fields") from None
