"""Tests for the deterministic LLM-output validator (output_validator.py).

Offline: no LLM, no I/O. Validates tool-call arguments against real-style
JSON schemas.

Leaf-module imports only — `import main` is hard-fenced by conftest.py.
"""

from __future__ import annotations

from output_validator import corrective_message, validate_tool_call

# A minimal combined schema list mirroring TOOL_DEFINITIONS shape.
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "vault_research",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "depth": {"type": "string", "enum": ["deep", "quick"]},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_gaps",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def test_valid_call_passes():
    assert (
        validate_tool_call("vault_research", {"topic": "faiss indexing"}, SCHEMAS) == []
    )


def test_valid_call_with_optional_enum_passes():
    assert (
        validate_tool_call("vault_research", {"topic": "x", "depth": "quick"}, SCHEMAS)
        == []
    )


def test_missing_required_flagged():
    problems = validate_tool_call("vault_research", {}, SCHEMAS)
    assert any("missing required parameter 'topic'" in p for p in problems)


def test_wrong_type_flagged():
    problems = validate_tool_call("vault_search", {"query": "x", "k": "five"}, SCHEMAS)
    assert any("must be type integer" in p for p in problems)


def test_bool_is_not_integer():
    # bool is a subclass of int in Python — must NOT pass an integer check.
    problems = validate_tool_call("vault_search", {"query": "x", "k": True}, SCHEMAS)
    assert any("must be type integer" in p for p in problems)


def test_unknown_parameter_flagged():
    problems = validate_tool_call(
        "vault_research", {"topic": "x", "topics": "y"}, SCHEMAS
    )
    assert any("unknown parameter 'topics'" in p for p in problems)


def test_bad_enum_flagged():
    problems = validate_tool_call(
        "vault_research", {"topic": "x", "depth": "deeper"}, SCHEMAS
    )
    assert any("must be one of" in p for p in problems)


def test_unknown_tool_flagged():
    problems = validate_tool_call("not_a_tool", {}, SCHEMAS)
    assert any("unknown tool" in p for p in problems)


def test_no_schema_tool_passes():
    # vault_gaps declares an empty properties map — any empty call is fine.
    assert validate_tool_call("vault_gaps", {}, SCHEMAS) == []


def test_non_dict_args_flagged():
    problems = validate_tool_call("vault_search", ["query", "x"], SCHEMAS)
    assert any("must be a JSON object" in p for p in problems)


def test_corrective_message_shape():
    msg = corrective_message("vault_research", ["missing required parameter 'topic'"])
    assert msg["validation_failed"] is True
    assert "NOT executed" in msg["error"]
    assert msg["problems"]
    assert "action_required" in msg
