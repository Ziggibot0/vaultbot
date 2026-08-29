"""Tests for the contributions-gated tool set (issue #298).

The read-only ``github_issues`` tool must be loadable/advertised even when
``VAULTBOT_ALLOW_CONTRIBUTIONS`` is off, so a fresh install can read issues.
The mutating tools (submit_contribution, review_contributions, torture_test,
pr_feedback) must remain gated — they author/merge code and must never be
advertised without the opt-in.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_github_issues_is_not_contributions_gated():
    """Read-only github_issues must load even with contributions off (#298)."""
    from self_improver import _CONTRIBUTIONS_GATED_TOOLS

    assert "github_issues" not in _CONTRIBUTIONS_GATED_TOOLS


def test_mutating_tools_remain_contributions_gated():
    """Code-authoring/merging tools stay gated behind the opt-in."""
    from self_improver import _CONTRIBUTIONS_GATED_TOOLS

    for tool in (
        "submit_contribution",
        "review_contributions",
        "torture_test",
        "pr_feedback",
    ):
        assert tool in _CONTRIBUTIONS_GATED_TOOLS, f"{tool} must stay gated"


def test_load_custom_tools_loads_github_issues_with_contributions_off(
    tmp_path, monkeypatch
):
    """With contributions off, github_issues is still loaded/advertised."""
    import self_improver as si_mod
    from self_improver import SelfImprover

    # Point CUSTOM_TOOLS_DIR at a temp dir with a minimal github_issues tool.
    custom_dir = tmp_path / "custom_tools"
    custom_dir.mkdir()
    (custom_dir / "__init__.py").write_text("", encoding="utf-8")
    (custom_dir / "github_issues.py").write_text(
        "SCHEMA = {'name': 'github_issues', 'description': 'read issues', "
        "'parameters': {'type': 'object', 'properties': {}}}\n"
        "def run(args):\n    return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(si_mod, "CUSTOM_TOOLS_DIR", custom_dir)
    monkeypatch.setenv("VAULTBOT_ALLOW_CONTRIBUTIONS", "false")

    improver = SelfImprover.__new__(SelfImprover)
    improver.session_logger = None
    improver._loaded_tools = {}
    improver._loaded_schemas = {}

    schemas = improver.load_custom_tools()
    assert "github_issues" in schemas
