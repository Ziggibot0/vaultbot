"""Tests for the idempotency helpers in custom_tools.textbook_ingest.

These tests exercise ONLY the idempotency primitives — `source_key`,
`find_prior_ingest`, and `remove_stale_notes` — not the full `run()` ingest
pipeline (which needs Ollama / marker / network). All filesystem state is
kept inside pytest's `tmp_path` so no real vault is touched.

Documentation grounding:
- pytest tmp_path: "a unique pathlib.Path object ... for the test."
  https://docs.pytest.org/en/stable/reference/reference.html
- Anatomy of a test (arrange / act / assert):
  https://docs.pytest.org/en/stable/explanation/anatomy.html
"""
from pathlib import Path

import pytest

from custom_tools.textbook_ingest import (
    source_key,
    find_prior_ingest,
    remove_stale_notes,
    _source_key_line,
)
import custom_tools.textbook_ingest as tbi


@pytest.fixture
def patched_dirs(tmp_path, monkeypatch):
    """Point the module's VAULT_DIR / TEXTBOOKS_DIR at tmp_path.

    `find_prior_ingest` globs TEXTBOOKS_DIR for `*-toc.md`, and
    `remove_stale_notes` writes/removes files under TEXTBOOKS_DIR and
    reports paths relative to VAULT_DIR. Monkeypatching lets every test
    use the isolated tmp_path instead of the real vault.
    """
    monkeypatch.setattr(tbi, "TEXTBOOKS_DIR", tmp_path)
    # VAULT_DIR must be an ancestor of TEXTBOOKS_DIR for the
    # `path.relative_to(VAULT_DIR)` call in remove_stale_notes to succeed.
    monkeypatch.setattr(tbi, "VAULT_DIR", tmp_path)
    return tmp_path


def _write_toc(tmp_path: Path, key: str, slugs_headings):
    """Write a TOC note carrying the source-key marker + wikilink entries.

    The marker format is the exact string produced by
    `_source_key_line(key)`:
        <!-- vaultbot:textbook-source-key <12-hex> -->

    Each entry follows the `[[slug|Heading]]` format that
    `find_prior_ingest` parses with the regex `\\[\\[([^\\]|]+)\\|`.
    """
    toc_path = tmp_path / "physics-toc.md"
    lines = [
        "# Physics Textbook",
                "",
                _source_key_line(key),
                "",
            ]
    for slug, heading in slugs_headings:
        lines.append("[[%s|%s]]" % (slug, heading))
    toc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return toc_path


def test_find_prior_ingest_round_trip(patched_dirs):
    """source_key → marker in TOC → find_prior_ingest recovers slugs.

    Arrange: write a TOC note with the hidden source-key marker and two
    `[[slug|Heading]]` entries into tmp_path (monkeypatched TEXTBOOKS_DIR).
    Act:   call find_prior_ingest(key) with key = source_key("physics").
    Assert: returns (toc_path, ["slug1", "slug2"]).
    """
    tmp_path = patched_dirs
    key = source_key("physics")
    toc_path = _write_toc(
        tmp_path,
        key,
        [("slug1", "Heading One"), ("slug2", "Heading Two")],
    )

    found_path, slugs = find_prior_ingest(key)

    assert found_path == toc_path
    assert slugs == ["slug1", "slug2"]


def test_remove_stale_notes_deletes_only_orphans(patched_dirs):
    """remove_stale_notes deletes only the slugs passed in.

    Arrange: create keep1.md, keep2.md, orphan.md in tmp_path.
    Act:   call remove_stale_notes(["orphan"]).
    Assert: keep1.md + keep2.md survive; orphan.md is gone; the returned
    list contains the removed path.
    """
    tmp_path = patched_dirs
    keep1 = tmp_path / "keep1.md"
    keep2 = tmp_path / "keep2.md"
    orphan = tmp_path / "orphan.md"
    for p in (keep1, keep2, orphan):
        p.write_text("body", encoding="utf-8")

    removed = remove_stale_notes(["orphan"])

    assert keep1.exists()
    assert keep2.exists()
    assert not orphan.exists()
    # remove_stale_notes reports paths relative to VAULT_DIR (= tmp_path).
    assert removed == ["orphan.md"]


def test_reingest_is_idempotent(patched_dirs):
    """A second find_prior_ingest on the same source returns identical
    slugs — the marker round-trip is stable, no drift or duplication.

    Also verifies the idempotency contract for remove_stale_notes: when
    the new slug set equals the old slug set, the stale set is empty and
    remove_stale_notes deletes nothing.
    """
    tmp_path = patched_dirs
    key = source_key("physics")
    toc_path = _write_toc(
        tmp_path,
        key,
        [("slug1", "Heading One"), ("slug2", "Heading Two")],
    )

    # First read — establishes the prior-ingest slugs.
    found_path_1, slugs_1 = find_prior_ingest(key)

    # Second read — a re-ingest would call find_prior_ingest again with
    # the same key. The marker + wikilink parsing must be deterministic.
    found_path_2, slugs_2 = find_prior_ingest(key)

    assert found_path_1 == toc_path
    assert found_path_2 == toc_path
    assert slugs_1 == ["slug1", "slug2"]
    assert slugs_2 == slugs_1  # same slugs, no duplicates, no drift

    # Idempotency contract for remove_stale_notes: stale = old - new.
    # When old == new, stale is empty, so nothing is removed.
    stale = list(set(slugs_2) - set(slugs_1))
    assert stale == []
    removed = remove_stale_notes(stale)
    assert removed == []
    # The two section notes (if they existed) are untouched; here we only
    # have the TOC, which remove_stale_notes never touches (it only
    # deletes slug-named files, not *-toc.md).
    assert toc_path.exists()