"""Deterministic LLM-output validation — the "scaffolding disposes" layer.

THE PROBLEM THIS SOLVES
-----------------------
Small local models (30B and especially 7B/3B) are unreliable at FUNCTION-CALL
FORMAT: they emit malformed tool arguments — missing required fields, wrong
types, hallucinated parameter names, JSON-with-trailing-text. Research
(arXiv:2504.19277) is unambiguous: "SLMs struggle significantly with adhering
to the given output format" — it's their #1 weakness. If a malformed call
executes, the tool crashes or does the wrong thing and the model can't tell
why, so it retries blindly and loops.

The deterministic-scaffolding answer (OpenEmpower sandwich): the MODEL
proposes a tool call; the FRAMEWORK validates it against the tool's declared
JSON schema BEFORE it runs. Malformed → reject with a precise, corrective
error message that tells the model *exactly* what to fix and retry — instead
of letting a broken call execute. This is "structured outputs only" enforced
at the framework layer, so a small model doesn't need perfect format
adherence — it needs to be "good enough that the scaffolding catches the rest."

WHAT THIS VALIDATES (deterministically, no LLM)
-----------------------------------------------
Given a tool's declared JSON schema (from TOOL_DEFINITIONS /
META_TOOL_DEFINITIONS / custom schemas) and the model's parsed arguments:
  1. required parameters present
  2. no unknown parameters (hallucinated arg names — a top small-model error)
  3. primitive types match (string/integer/number/boolean/array/object)
  4. enum values respected
Returns a list of human-readable problems, or [] if valid. The chat loop
turns a non-empty list into a corrective tool-result and skips execution.

Pure stdlib. No LLM calls. No I/O.
"""
from __future__ import annotations

from typing import Any

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool)),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _schema_for(tool_name: str, schemas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find a tool's parameter schema by name across the combined schema list."""
    for s in schemas:
        if not isinstance(s, dict):
            continue
        fn = s.get("function", {})
        if fn.get("name") == tool_name:
            return fn.get("parameters") or {}
    return None


def validate_tool_call(tool_name: str,
                       args: dict[str, Any],
                       schemas: list[dict[str, Any]]) -> list[str]:
    """Validate a tool call's arguments against its declared schema.

    Args:
        tool_name: The tool the model asked to call.
        args: The parsed arguments dict (after json.loads).
        schemas: The combined schema list (TOOL_DEFINITIONS +
                 META_TOOL_DEFINITIONS + custom schemas).

    Returns:
        A list of problem strings (empty = valid). Each problem names the
        exact issue so the model can correct it on retry.
    """
    problems: list[str] = []

    if not isinstance(args, dict):
        return [f"arguments must be a JSON object, got {type(args).__name__}"]

    schema = _schema_for(tool_name, schemas)
    if schema is None:
        # Unknown tool — that's a different (also deterministic) failure.
        return [f"unknown tool: '{tool_name}' (not in available tools)"]

    if not schema:
        return problems  # tool declares no parameter schema — nothing to check

    props = schema.get("properties", {})
    required = schema.get("required", [])

    # 1. required params present (and not null/empty-string for required strings)
    for req in required:
        if req not in args:
            problems.append(f"missing required parameter '{req}'")
        elif args[req] is None:
            problems.append(f"required parameter '{req}' is null")

    # 2. no unknown params (hallucinated arg names)
    if props:
        for key in args:
            if key not in props:
                known = ", ".join(sorted(props))
                problems.append(
                    f"unknown parameter '{key}' (valid: {known or 'none'})")

    # 3. types + enums for provided args
    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if (expected in _TYPE_CHECKS and value is not None
                and not _TYPE_CHECKS[expected](value)):
            problems.append(
                f"parameter '{key}' must be type {expected}, "
                f"got {type(value).__name__}")
        enum = spec.get("enum")
        if enum and value is not None and value not in enum:
            problems.append(
                f"parameter '{key}' must be one of {enum}, got {value!r}")

    return problems


def corrective_message(tool_name: str, problems: list[str]) -> dict[str, Any]:
    """Shape a corrective tool-result the model reads and acts on.

    Tells the model the call was NOT executed, lists every problem, and
    instructs a retry with corrected arguments. Mirrors the fail-safe
    philosophy: never execute an unvalidated call, always explain the fix.
    """
    return {
        "error": f"invalid arguments for '{tool_name}' — call NOT executed",
        "validation_failed": True,
        "problems": problems,
        "action_required": (
            "Fix the listed problems and re-issue the tool call with "
            "corrected arguments. Do not change the tool name — only the "
            "argument values/structure."),
    }
