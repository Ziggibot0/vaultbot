"""Exhaustive type-coverage tests for utility functions that handle
multiple input types. These catch the class of bug where a function is
written for the happy path (one type) and returns ``None`` for the rest,
crashing a caller that slices the result (``summary[:200]`` on ``None``).

Bug caught: 2026-07-30, ``tool_result_summary`` only handled the
non-dict branch and returned ``None`` for dicts; ``chat_handler.py``
did ``tool_result_summary(...)[:200]`` and crashed the agentic loop
with ``'NoneType' object is not subscriptable``.

The invariant under test: **these functions MUST return a ``str`` for
every plausible input type, never ``None``.** A caller is allowed to
slice or concatenate the result; a ``None`` return is a contract
violation.
"""

import pytest

pytestmark = pytest.mark.unit

from chat_helpers import tool_result_summary


# Every plausible tool-result type. Tool results are JSON-decoded, so
# they're one of: dict, list, str, int, float, bool, None. We also test
# bytes (a defensive edge) and a deeply-nested dict (realistic tool
# output).
@pytest.mark.parametrize(
    "label,result",
    [
        ("str", "some text"),
        ("empty str", ""),
        ("dict empty", {}),
        ("dict with results", {"results": ["a", "b", "c"]}),
        ("dict with error", {"error": "boom"}),
        (
            "dict vault_research",
            {"source_count": 3, "synthesis_facts": 12, "note_path": "/vault/Note.md"},
        ),
        ("dict unknown tool", {"foo": 1, "bar": [2, 3]}),
        ("dict custom tool result key", {"result": "did the thing"}),
        ("list", ["a", "b"]),
        ("empty list", []),
        ("int", 42),
        ("float", 3.14),
        ("bool true", True),
        ("bool false", False),
        ("None", None),
        ("bytes", b"raw"),
        ("nested dict", {"a": {"b": {"c": "deep"}}, "results": [1, 2]}),
    ],
)
def test_tool_result_summary_never_returns_none(label, result):
    """For EVERY plausible tool-result type, the summary must be a str
    (never None). A None return would crash any caller that slices it
    (``summary[:200]`` → TypeError). The function's contract is "always
    a string".
    """
    summary = tool_result_summary("any_tool", result)
    assert summary is not None, (
        f"tool_result_summary returned None for input type '{label}' "
        f"(result={result!r}). Callers do `summary[:200]` which crashes "
        f"with 'NoneType object is not subscriptable'. The function must "
        f"return a str for every input type."
    )
    assert isinstance(summary, str), (
        f"tool_result_summary returned {type(summary).__name__} (not str) "
        f"for input type '{label}'. Callers expect a str."
    )


def test_tool_result_summary_known_tools_have_specific_summaries():
    """The per-tool branches must fire for known tool names and produce
    a meaningful (non-empty) one-liner. This guards against a refactor
    that collapses the per-tool branches back into the generic fallback
    (which would still return a str but lose the useful summary).
    """
    assert "notes found" in tool_result_summary(
        "vault_search", {"results": [{"title": "x"}]}
    )
    assert "gaps found" in tool_result_summary("vault_gaps", {"count": 5})
    assert "error" in tool_result_summary("vault_research", {"error": "timeout"})


def test_tool_result_summary_is_safely_sliceable():
    """The exact call-site pattern in chat_handler.py:
    ``tool_result_summary(tool_name, tool_result)[:200]`` must never raise.
    Verify it for a representative spread of types (including the dict
    case that previously returned None).
    """
    for result in [{}, {"results": []}, "text", 1, None, ["a"], True]:
        s = tool_result_summary("vault_search", result)
        # This is the line that crashed before the fix.
        _ = s[:200]  # must not raise
