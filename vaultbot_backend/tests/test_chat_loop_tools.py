"""Regression tests for chat_loop_tools.py — the per-tool-call execution loop.

Covers the two pure-logic fixes that were previously untested:

1. ``is_malformed_tool_name`` (issue #130): under context bloat the model
   emits a "tool name" that is actually prior tool-result text. The guard
   must reject it so the garbled blob is never echoed back into context.

2. The read-cap classification (issue #128): ``github_issues`` must be
   treated as a read tool (generous cap) so issue bodies/comments aren't
   truncated mid-sentence.

No network, no Ollama, no Services — pure functions only.
"""

from __future__ import annotations

import pytest
from chat_loop_tools import is_malformed_tool_name

pytestmark = pytest.mark.unit


class TestIsMalformedToolName:
    """issue #130 — garbled tool-call names from context bloat."""

    def test_valid_simple_name(self):
        assert is_malformed_tool_name("code_read") is False

    def test_valid_underscored_name(self):
        assert is_malformed_tool_name("vault_read_note") is False

    def test_valid_with_digits(self):
        assert is_malformed_tool_name("tool2") is False

    def test_empty_name(self):
        assert is_malformed_tool_name("") is True

    def test_none_name(self):
        assert is_malformed_tool_name(None) is True

    def test_whitespace_name(self):
        assert is_malformed_tool_name("code read") is True

    def test_newline_name(self):
        assert is_malformed_tool_name("code\nread") is True

    def test_json_braces_name(self):
        # The exact failure mode: prior tool-result JSON smashed into the
        # name field.
        assert is_malformed_tool_name('code_read({"file_path": "x"})') is True

    def test_colon_name(self):
        assert is_malformed_tool_name("Tool call: code_read") is True

    def test_overlong_name(self):
        # A ~2000-char blob of prior tool-result text.
        assert is_malformed_tool_name("x" * 2000) is True

    def test_boundary_64_chars_ok(self):
        assert is_malformed_tool_name("a" * 64) is False

    def test_boundary_65_chars_rejected(self):
        assert is_malformed_tool_name("a" * 65) is True


class TestReadCapClassification:
    """issue #128 — github_issues must get the generous read cap.

    The read-cap set lives inline in execute_round_tools. We assert the
    classification indirectly by checking the source of truth: the tuple
    of read tools. This guards against a future refactor silently dropping
    github_issues back to the 10K standard cap.
    """

    def test_github_issues_is_a_read_tool(self):
        # The read-cap branch in execute_round_tools checks membership in
        # this exact tuple. If github_issues is removed, issue #128
        # regresses (issue bodies get truncated mid-sentence).
        import inspect

        import chat_loop_tools

        src = inspect.getsource(chat_loop_tools.execute_round_tools)
        assert '"github_issues"' in src
        assert '"code_read"' in src
        assert '"vault_read_note"' in src
