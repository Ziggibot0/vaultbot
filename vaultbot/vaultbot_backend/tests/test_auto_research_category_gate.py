"""Tests for the auto-research category gate (issue #25).

Verifies that auto-research is skipped when Route-Task classifies a
message as a non-research category (e.g. "conversational"), and that
the TUNABLES.auto_research_categories set is correctly configured.

Run: pytest tests/test_auto_research_category_gate.py -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestAutoResearchCategoryGate:
    """Test the category gate that prevents auto-research on
    conversational backchannels."""

    def test_conversational_not_in_research_categories(self):
        """'conversational' must NOT be in auto_research_categories."""
        from config import TUNABLES

        assert "conversational" not in TUNABLES.auto_research_categories

    def test_research_in_research_categories(self):
        """'research' must be in auto_research_categories."""
        from config import TUNABLES

        assert "research" in TUNABLES.auto_research_categories

    def test_gap_filling_in_research_categories(self):
        """'gap-filling' must be in auto_research_categories."""
        from config import TUNABLES

        assert "gap-filling" in TUNABLES.auto_research_categories

    def test_unknown_in_research_categories(self):
        """'unknown' must be in auto_research_categories (fallback safety)."""
        from config import TUNABLES

        assert "unknown" in TUNABLES.auto_research_categories

    def test_question_answering_not_in_research_categories(self):
        """'question-answering' should NOT trigger auto-research —
        the vault already has the info, we just need to answer."""
        from config import TUNABLES

        assert "question-answering" not in TUNABLES.auto_research_categories

    def test_code_editing_not_in_research_categories(self):
        """'code-editing' should NOT trigger auto-research."""
        from config import TUNABLES

        assert "code-editing" not in TUNABLES.auto_research_categories

    def test_category_allows_research_logic(self):
        """Test the category gate logic: empty category falls through
        (backward compat), non-research categories block, research
        categories allow."""
        from config import TUNABLES

        # Simulate the gate logic from chat_turn_prep.py
        def _category_allows_research(category: str) -> bool:
            return not category or category in TUNABLES.auto_research_categories

        # Empty category (trivial message, Route-Task failure) → allow
        # (backward compatibility — don't break existing behavior)
        assert _category_allows_research("") is True

        # Research-worthy categories → allow
        assert _category_allows_research("research") is True
        assert _category_allows_research("gap-filling") is True
        assert _category_allows_research("unknown") is True

        # Non-research categories → block
        assert _category_allows_research("conversational") is False
        assert _category_allows_research("question-answering") is False
        assert _category_allows_research("code-editing") is False
        assert _category_allows_research("fact-checking") is False
        assert _category_allows_research("vault-maintenance") is False
        assert _category_allows_research("self-improvement") is False
        assert _category_allows_research("chat-consolidation") is False