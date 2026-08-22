"""Tests for the method-aware auth gate (issue #253 / #230).

Verifies that mutating methods (POST/PUT/DELETE/PATCH) on /llm/* require
auth, while GET stays open and the explicit always-required paths still
require auth regardless of method.

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
        ],
    )
    def test_mutating_llm_requires_auth(self, path, method):
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
        ],
    )
    def test_read_llm_does_not_require_auth(self, path, method):
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
