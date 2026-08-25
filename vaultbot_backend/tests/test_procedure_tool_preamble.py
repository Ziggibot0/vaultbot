"""Regression tests for procedure_tool_preamble.py — the injected
``llm_generate`` wrapper (issue #137).

The per-claim entailment loop in Verify-Answer-Entailment was unbounded:
each ``llm_generate`` call had no ``max_predict`` and no per-call timeout,
so a stalled small model could hang the whole procedure for minutes-to-hours.

The fix injects ``max_predict=256`` and ``timeout=30`` into the generated
``llm_generate`` wrapper. This test guards against a future refactor that
silently drops those bounds.
"""

from __future__ import annotations

import pytest
from procedure_tool_preamble import _build_tool_preamble

pytestmark = pytest.mark.unit


class TestLlmGenerateBounds:
    """issue #137 — bounded per-call entailment."""

    def test_llm_generate_has_max_predict(self):
        preamble = _build_tool_preamble(["llm_generate"])
        assert "max_predict=256" in preamble

    def test_llm_generate_has_timeout(self):
        preamble = _build_tool_preamble(["llm_generate"])
        assert "timeout=30" in preamble

    def test_llm_generate_uses_think_flag(self):
        # think=_think must still be passed (small cartridge disables
        # reasoning) — the timeout/max_predict must not have dropped it.
        preamble = _build_tool_preamble(["llm_generate"])
        assert "think=_think" in preamble

    def test_llm_generate_allows_override_args(self):
        preamble = _build_tool_preamble(["llm_generate"])
        assert "def llm_generate(prompt, system=" in preamble
        assert "max_predict=256" in preamble
        assert "timeout=30" in preamble
        assert "max_predict=max_predict" in preamble
        assert "timeout=timeout" in preamble

    def test_no_llm_generate_when_not_allowed(self):
        # A procedure without llm_generate in allowed_tools must not inject
        # the wrapper at all.
        preamble = _build_tool_preamble(["vault_search"])
        assert "def llm_generate" not in preamble
