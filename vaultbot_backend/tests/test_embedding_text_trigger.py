"""Tests for trigger/inhibitor support in _embedding_text_for_note.

Verifies that the procedure embedding surface prefers the `trigger` list
over the legacy `when_to_use` string, excludes inhibitor phrases, and falls
back to `when_to_use` when `trigger` is absent.

These tests call the static method directly — no Ollama, no FAISS, no disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from vault_indexer import VaultIndexer


def _embedding_text(content: str, stem: str = "Test-Procedure") -> str:
    """Call the static _embedding_text_for_note directly (no indexer needed)."""
    return VaultIndexer._embedding_text_for_note(Path(f"/vault/{stem}.md"), content)


# ── Fixtures ────────────────────────────────────────────────────────────

_BASE_FM = """---
type: procedure
status: verified
description: "Check a note's claims against its sources"
"""


def _procedure(content_body: str, fm_suffix: str = "") -> str:
    """Build a procedure note with frontmatter + body."""
    fm = _BASE_FM + fm_suffix
    return fm + "---\n\n" + content_body


# ── trigger list preferred over when_to_use ─────────────────────────────


def test_trigger_list_preferred_over_when_to_use():
    """When both trigger and when_to_use exist, trigger phrases are used."""
    content = _procedure(
        "Body text.",
        fm_suffix=(
            'when_to_use: "legacy when clause"\n'
            "trigger:\n"
            '  - "when checking syntax"\n'
            '  - "when verifying imports"\n'
        ),
    )
    surface = _embedding_text(content)
    assert "Use when: when checking syntax" in surface
    assert "Use when: when verifying imports" in surface
    # Legacy when_to_use is NOT used when trigger is present.
    assert "Use when: legacy when clause" not in surface


def test_falls_back_to_when_to_use_when_no_trigger():
    """When trigger is absent, when_to_use is split into clauses."""
    content = _procedure(
        "Body text.",
        fm_suffix='when_to_use: "when X, when Y, or when Z"\n',
    )
    surface = _embedding_text(content)
    # The split produces "X", "Y", "Z" clauses.
    assert "Use when: X" in surface or "Use when:" in surface
    assert "legacy" not in surface.lower()


def test_inhibitor_not_in_embedding_text():
    """Inhibitor phrases must NOT appear in the embedding surface.

    Including inhibitors would pull the note's vector toward inhibitor-
    matching queries — the opposite of what we want.  Inhibitors are only
    in the trigger_store for the gate.
    """
    content = _procedure(
        "Body text.",
        fm_suffix=(
            "trigger:\n"
            '  - "when verifying claims"\n'
            "inhibitor:\n"
            '  - "when the user just wants a summary"\n'
        ),
    )
    surface = _embedding_text(content)
    assert "when verifying claims" in surface
    assert "when the user just wants a summary" not in surface


def test_no_trigger_no_when_degrades_to_full_content():
    """When a procedure has no trigger, no when_to_use, AND no description,
    the full content is used as a degraded fallback."""
    body = "This is the full body of the procedure."
    # No description, no when_to_use, no trigger → degraded path returns
    # the full content (which includes the body).
    content = "---\ntype: procedure\nstatus: verified\n---\n\n" + body
    surface = _embedding_text(content, stem="NoDesc")
    assert body in surface


def test_trigger_inline_single_value():
    """A single inline trigger value (not a list) is still picked up."""
    content = _procedure(
        "Body text.",
        fm_suffix='trigger: "when checking syntax inline"\n',
    )
    surface = _embedding_text(content)
    assert "Use when: when checking syntax inline" in surface


def test_description_always_included():
    """The description is always in the embedding surface."""
    content = _procedure(
        "Body text.",
        fm_suffix=('trigger:\n  - "when checking syntax"\n'),
    )
    surface = _embedding_text(content)
    assert "Check a note's claims against its sources" in surface
