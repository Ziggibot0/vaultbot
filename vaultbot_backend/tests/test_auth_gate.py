"""Tests for the method-aware auth gate (issue #253 / #230).

Verifies that ANY mutating method (POST/PUT/DELETE/PATCH) requires auth,
regardless of path, while GET stays open and the explicit always-required
paths still require auth regardless of method.

Run: pytest tests/test_auth_gate.py -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from auth import is_auth_required_for_method  # noqa: E402


class TestAuthRequiredForMethod:
    @pytest.mark.parametrize(
        "path,method",
        [
            ("/llm/providers", "POST"),
            ("/llm/providers", "DELETE"),
            ("/llm/providers/foo", "DELETE"),
            ("/llm/models", "POST"),
            ("/llm/models/foo", "DELETE"),
            ("/llm/role", "POST"),
            ("/llm/test_model", "POST"),
            ("/llm/set_model", "POST"),
            ("/llm/providers", "PUT"),
            ("/llm/providers", "PATCH"),
            # issue #230: every mutating endpoint, not just /llm/*
            ("/restart", "POST"),
            ("/reload-plugin", "POST"),
            ("/update/rollback", "POST"),
            ("/config", "POST"),
            ("/models/pull", "POST"),
            ("/set_model", "POST"),
            ("/research_tool", "POST"),
            ("/ingest_learning_material", "POST"),
            ("/task", "POST"),
            ("/tournament/run", "POST"),
            ("/tournament/staging", "POST"),
            ("/tournament/staging/clear", "POST"),
            ("/tournament/staging/abc", "DELETE"),
            ("/broadcast_questionnaire", "POST"),
            ("/user_response", "POST"),
            ("/stt", "POST"),
            ("/tts", "POST"),
        ],
    )
    def test_mutating_requires_auth(self, path, method):
        assert is_auth_required_for_method(path, method) is True

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/llm/providers", "GET"),
            ("/llm/models/all", "GET"),
            ("/llm/providers/foo/live_models", "GET"),
            ("/llm/vision_check", "GET"),
            ("/models", "GET"),
            ("/health", "GET"),
            ("/config/effective", "GET"),
            ("/custom_tools", "GET"),
            ("/tournament/models", "GET"),
            ("/sessions", "GET"),
        ],
    )
    def test_read_does_not_require_auth(self, path, method):
        assert is_auth_required_for_method(path, method) is False

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/custom_tools/call", "POST"),
            ("/custom_tools/call", "GET"),  # always-required regardless of method
            ("/shutdown", "POST"),
            ("/ws", "GET"),
        ],
    )
    def test_always_required_paths(self, path, method):
        assert is_auth_required_for_method(path, method) is True
