"""Tests for `abstract_context.build_abstract_context`.

These are leaf-module-only tests — they never `import main`. Each test
builds a throwaway vault under the pytest `tmp_path` fixture (a unique
`pathlib.Path` per test that is auto-cleaned at teardown), constructs a
minimal `VaultGraph` over it, and exercises `build_abstract_context`
in the Arrange → Act → Assert style.

Documentation grounding:
- tmp_path: unique per-test pathlib.Path, auto-cleaned.
  https://docs.pytest.org/en/stable/reference/reference.html
- monkeypatch: auto-undone stubs.
  https://docs.pytest.org/en/stable/reference/reference.html
- Anatomy of a test: Arrange → Act → Assert → Cleanup.
  https://docs.pytest.org/en/stable/explanation/anatomy.html
"""

from abstract_context import build_abstract_context
from vault_graph import VaultGraph

# ---------------------------------------------------------------------------
# Test 1 — legacy fallback when no L1 cards exist
# ---------------------------------------------------------------------------

def test_falls_back_to_legacy_when_no_cards(tmp_path):
    """Arrange → Act → Assert.

    With a vault that contains a single L0 note and NO `*-L1.md` cards,
    `build_abstract_context` must degrade to the legacy
    `build_graph_context` content dump so the chat loop never breaks.
    """
    # ---- Arrange ----
    # One L0 note; no co-located card, so the abstract path finds no L1
    # highway and falls back to the legacy builder.
    l0_path = tmp_path / "my-note.md"
    l0_path.write_text(
        "# My Note\n\nThis is a standalone note with no concept card.\n"
        "It has a few sentences so the graph node has real content.\n",
        encoding="utf-8",
    )

    # VaultGraph scans .md files under vault_path at construction time.
    vg = VaultGraph(vault_path=str(tmp_path))
    assert "my-note" in vg.nodes, "precondition: graph must index the note"

    search_results = [
        {"file_path": str(l0_path), "score": 0.9},
    ]

    # ---- Act ----
    result = build_abstract_context(
        graph=vg,
        search_results=search_results,
        query="test",
        k=5,
        textbooks_dir=str(tmp_path),
    )

    # ---- Assert ----
    # The fallback marker is "legacy" and the context is a non-empty
    # string (the legacy builder dumps the note's content).
    assert result["resolution"] == "legacy", result
    assert isinstance(result["context"], str) and result["context"].strip(), (
        "legacy context must be a non-empty string"
    )
    assert result["drill_down_used"] is False, result
    assert result["l0_drill"] is None, result
    assert result["l1_cards"] == 0, result
    # Sanity: the legacy context should mention the note we wrote.
    assert "My Note" in result["context"] or "my-note" in result["context"]


# ---------------------------------------------------------------------------
# Test 2 — L0 drill-down includes the FULL L0 content (no 2000-char cut)
# ---------------------------------------------------------------------------

def test_l1_drill_down_includes_full_l0(tmp_path):
    """Arrange → Act → Assert.

    With an L0 note that has a co-located `-L1.md` card, the abstract
    path is taken and the L0 drill-down must contain the FULL raw L0
    content — not the first 2000 chars (the legacy truncation). This is
    the core promise of the multi-resolution context: detail is never
    lost to truncation for the single top seed.
    """
    # ---- Arrange ----
    # A long L0 note (>5000 chars of repeated prose) so any 2000-char
    # truncation would be obvious to detect.
    body_block = (
        "The hippocampal index theory proposes that the hippocampus stores "
        "an index of neocortical traces rather than the traces themselves. "
        "Retrieval reactivates the index which pointers back to the cortex. "
    )
    # ~3000 chars — comfortably over the old 2000 cut but under the
    # production DRILL_CAP of 12000 (so the cap doesn't mask the test).
    long_body = body_block * 16
    l0_path = tmp_path / "my-note.md"
    l0_path.write_text(
        f"# My Note\n\n{long_body}\n",
        encoding="utf-8",
    )
    assert len(l0_path.read_text(encoding="utf-8")) > 2000

    # A co-located L1 concept card with a short extractive summary and
    # the `> source: [[my-note]]` pointer that the drill-down resolves via.
    card_path = tmp_path / "my-note-L1.md"
    card_path.write_text(
        "# My Note (concept card)\n"
        "> source: [[my-note]]\n"
        "\n"
        "Hippocampal index: hippocampus stores pointers to neocortical "
        "memory traces, not the traces themselves.\n",
        encoding="utf-8",
    )

    vg = VaultGraph(vault_path=str(tmp_path))
    assert "my-note" in vg.nodes, "precondition: graph must index the L0"
    assert "my-note-l1" in vg.nodes, "precondition: graph must index the L1"

    search_results = [
        {"file_path": str(l0_path), "score": 0.95},
    ]

    # ---- Act ----
    result = build_abstract_context(
        graph=vg,
        search_results=search_results,
        query="hippocampal index",
        k=5,
        textbooks_dir=str(tmp_path),
    )

    # ---- Assert ----
    assert result["resolution"] == "abstract", result
    assert result["drill_down_used"] is True, result
    assert result["l1_cards"] >= 1, result

    # The drill-down path is the L0 stem (not the full content here —
    # the returned `l0_drill` is the stem string per the function's
    # return contract). The full L0 content must appear in `context`.
    assert result["l0_drill"] == "my-note", result
    ctx = result["context"]
    # The L0 drill section must contain the full body, not a 2000-char
    # truncation. Repeated prose means any truncation < len(long_body).
    assert long_body in ctx, (
        "L0 drill-down must include the FULL untruncated L0 body; "
        f"got context len={len(ctx)}"
    )
    # Belt-and-suspenders: the drill section must be far over the old
    # 2000-char cut, proving no truncation at 2000.
    drill_marker = "--- L0: DRILL-DOWN"
    assert drill_marker in ctx, ctx
    drill_section = ctx.split(drill_marker, 1)[1]
    assert len(drill_section) > 2000, (
        f"drill section must exceed the old 2000-char cut; got {len(drill_section)}"
    )
