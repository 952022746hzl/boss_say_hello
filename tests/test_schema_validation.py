import copy
import json

import pytest

from boss_say_hello.agent.tool_schema import (
    ASSESSMENT,
    BACKGROUND,
    SchemaValidationError,
    obj,
    validate_value,
)

SCHEMA = obj({"assessment": ASSESSMENT, "background": BACKGROUND})
VALID = {
    "assessment": {"score": 80, "reason": "Relevant", "evidence": [], "gaps": [], "missing": []},
    "background": {
        "identity_verified": True,
        "employment_checked": True,
        "outsourcing": "clear",
        "score": 60,
        "reason": "Verified",
        "source_ids": ["e1"],
    },
}
PRIVATE = "private-key-resume-sentinel"


@pytest.mark.parametrize(
    "section,key,value,path,reason,expected_part,actual_type",
    [
        ("assessment", "score", "80", "$.assessment.score", "type", "integer", "string"),
        ("assessment", "score", 80.0, "$.assessment.score", "type", "integer", "number"),
        ("assessment", "score", True, "$.assessment.score", "type", "integer", "boolean"),
        ("assessment", "score", -1, "$.assessment.score", "range", "0", "integer"),
        ("assessment", "score", 101, "$.assessment.score", "range", "100", "integer"),
        (
            "background",
            "identity_verified",
            1,
            "$.background.identity_verified",
            "type",
            "boolean",
            "integer",
        ),
        ("background", "outsourcing", PRIVATE, "$.background.outsourcing", "enum", "unknown", "string"),
        ("assessment", "reason", "  ", "$.assessment.reason", "min_length", "1", "string"),
        ("assessment", "reason", PRIVATE * 100, "$.assessment.reason", "max_length", "2000", "string"),
        ("assessment", "evidence", [PRIVATE] * 31, "$.assessment.evidence", "max_items", "30", "array"),
        ("assessment", "evidence", [None], "$.assessment.evidence[0]", "type", "string", "null"),
        ("assessment", "missing", None, "$.assessment.missing", "type", "array", "null"),
    ],
    ids=[
        "score-string",
        "score-float",
        "score-bool",
        "score-low",
        "score-high",
        "bool-integer",
        "outsourcing-enum",
        "blank-reason",
        "long-reason",
        "long-list",
        "null-item",
        "null-list",
    ],
)
def test_invalid_fields_report_safe_path_constraint_and_type(
    section, key, value, path, reason, expected_part, actual_type
):
    payload = copy.deepcopy(VALID)
    payload[section][key] = value
    with pytest.raises(ValueError) as caught:
        validate_value(payload, SCHEMA)
    error = caught.value
    assert isinstance(error, SchemaValidationError)
    assert error.path == path
    assert error.reason == reason
    assert expected_part in error.expected
    assert error.actual_type == actual_type
    assert path in str(error) and expected_part in str(error) and actual_type in str(error)
    assert PRIVATE not in str(error) + json.dumps(vars(error))


@pytest.mark.parametrize("section,key", [("assessment", "missing"), ("background", "source_ids")])
def test_missing_fields_report_the_schema_owned_path(section, key):
    payload = copy.deepcopy(VALID)
    del payload[section][key]
    with pytest.raises(ValueError) as caught:
        validate_value(payload, SCHEMA)
    error = caught.value
    assert isinstance(error, SchemaValidationError)
    assert error.path == f"$.{section}.{key}"
    assert error.reason == "missing_field"
    assert "array" in error.expected
    assert error.actual_type == "missing"


@pytest.mark.parametrize("section", [None, "assessment", "background"])
def test_extra_keys_and_values_never_enter_errors(section):
    payload = copy.deepcopy(VALID)
    target = payload if section is None else payload[section]
    target[PRIVATE + "-key"] = {"nested": PRIVATE + "-value"}
    with pytest.raises(ValueError) as caught:
        validate_value(payload, SCHEMA)
    error = caught.value
    assert isinstance(error, SchemaValidationError)
    assert error.path == ("$" if section is None else f"$.{section}")
    assert error.reason == "unexpected_fields"
    assert error.actual_type == "object"
    assert PRIVATE not in str(error) + json.dumps(vars(error))


def test_nested_enum_reports_index_without_returning_the_invalid_value():
    schema = {"type": "array", "items": {"type": "string", "enum": ["clear", "unknown"]}}
    with pytest.raises(ValueError) as caught:
        validate_value(["clear", PRIVATE], schema)
    error = caught.value
    assert isinstance(error, SchemaValidationError)
    assert error.path == "$[1]"
    assert error.reason == "enum"
    assert "clear" in error.expected and "unknown" in error.expected
    assert PRIVATE not in str(error) + json.dumps(vars(error))


def test_actual_type_uses_a_fixed_name_for_non_json_objects():
    private_type = type(PRIVATE, (), {"__str__": lambda self: PRIVATE})
    with pytest.raises(ValueError) as caught:
        validate_value(private_type(), {"type": "string"})
    error = caught.value
    assert isinstance(error, SchemaValidationError)
    assert error.actual_type == "unknown"
    assert PRIVATE not in str(error) + json.dumps(vars(error))


@pytest.mark.parametrize("score", [0, 100])
def test_valid_boundaries_and_empty_arrays_are_still_accepted(score):
    payload = copy.deepcopy(VALID)
    payload["assessment"].update(score=score, reason="x" * 2000, evidence=["x"] * 30)
    validate_value(payload, SCHEMA)


@pytest.mark.parametrize("value", [None, "text"])
def test_existing_nullable_type_union_remains_supported(value):
    validate_value(value, {"type": ["string", "null"]})
