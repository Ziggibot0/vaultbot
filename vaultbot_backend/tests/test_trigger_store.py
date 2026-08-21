"""Tests for the trigger/inhibitor store (trigger_store.py).

Uses a fake embedding getter that returns deterministic fixed vectors so the
tests are Ollama-free and deterministic.  State JSON is written to tmp_path so
no state leaks between tests.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.unit

from trigger_store import TriggerStore

# ── Helpers ──────────────────────────────────────────────────────────────


def _unit(vec: list[float]) -> np.ndarray:
    """Return a unit-length ndarray for a plain list."""
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _make_store(tmp_path: Path, getter=None) -> TriggerStore:
    """Build a TriggerStore with a tmp state path."""
    return TriggerStore(
        state_path=tmp_path / "trigger_store.json",
        embedding_getter=getter,
    )


def _fake_getter_factory(dim: int = 8):
    """Return an embedding getter that maps strings to fixed unit vectors.

    Each distinct string gets a deterministic vector derived from its hash so
    different phrases produce different (but reproducible) embeddings.
    """
    import hashlib

    def _get(text: str) -> np.ndarray:
        h = hashlib.sha256(text.encode()).digest()
        # Take enough bytes for dim, repeat if needed.
        vals = np.frombuffer((h * ((dim // len(h)) + 1))[:dim], dtype=np.uint8)
        v = vals.astype(np.float32) - 128.0
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    return _get


# ── update_note + check ──────────────────────────────────────────────────


def test_update_then_check_trigger_dominates(tmp_path):
    """A note with a trigger that matches the query → not dropped."""
    getter = _fake_getter_factory()
    store = _make_store(tmp_path, getter)
    # Use a phrase whose embedding we can reproduce for the query.
    trigger_phrase = "when verifying research claims"
    store.update_note(
        "/vault/Verify-Claims.md",
        trigger_phrases=[trigger_phrase],
        inhibitor_phrases=[],
    )
    # Query with the SAME text → max similarity ≈ 1.0 against the trigger.
    q = _unit(getter(trigger_phrase))
    should_drop, trig, inib = store.check(q, "/vault/Verify-Claims.md", margin=0.05)
    assert not should_drop, "trigger match should keep the note"
    assert trig > 0.99, f"trigger score should be ~1.0, got {trig}"
    assert inib == 0.0, "no inhibitors → inhibitor score 0"


def test_inhibitor_dominates_drops_note(tmp_path):
    """A note whose inhibitor matches the query more than its trigger → dropped."""
    getter = _fake_getter_factory()
    store = _make_store(tmp_path, getter)
    inhibitor_phrase = "when the user just wants a quick summary"
    trigger_phrase = "when verifying research claims"
    store.update_note(
        "/vault/Verify-Claims.md",
        trigger_phrases=[trigger_phrase],
        inhibitor_phrases=[inhibitor_phrase],
    )
    # Query matches the inhibitor strongly, trigger weakly.
    q = _unit(getter(inhibitor_phrase))
    should_drop, trig, inib = store.check(q, "/vault/Verify-Claims.md", margin=0.05)
    assert should_drop, "inhibitor match should drop the note"
    assert inib > trig + 0.05


def test_trigger_slightly_high_not_dropped(tmp_path):
    """When trigger >= inhibitor, the note is kept even if inhibitor matches."""
    getter = _fake_getter_factory()
    store = _make_store(tmp_path, getter)
    trigger_phrase = "when verifying research claims against sources"
    inhibitor_phrase = "when the note has no claims"
    store.update_note(
        "/vault/Verify-Claims.md",
        trigger_phrases=[trigger_phrase],
        inhibitor_phrases=[inhibitor_phrase],
    )
    # Query matches the trigger.
    q = _unit(getter(trigger_phrase))
    should_drop, trig, inib = store.check(q, "/vault/Verify-Claims.md", margin=0.05)
    assert not should_drop
    assert trig >= inib


def test_no_entry_passthrough(tmp_path):
    """A note with no trigger/inhibitor entry → passthrough (not dropped)."""
    store = _make_store(tmp_path, _fake_getter_factory())
    q = np.ones(8, dtype=np.float32)
    q = q / np.linalg.norm(q)
    should_drop, trig, inib = store.check(q, "/vault/unknown.md", margin=0.05)
    assert not should_drop
    assert trig == 0.0
    assert inib == 0.0


def test_empty_phrases_removes_entry(tmp_path):
    """update_note with both lists empty removes the entry (stale gate cleanup)."""
    getter = _fake_getter_factory()
    store = _make_store(tmp_path, getter)
    store.update_note(
        "/vault/note.md",
        trigger_phrases=["some trigger"],
        inhibitor_phrases=[],
    )
    assert "/vault/note.md" not in store.store or store.store["/vault/note.md"]
    # Now clear both → entry removed.
    store.update_note("/vault/note.md", trigger_phrases=[], inhibitor_phrases=[])
    # Path is resolved in the store; check by resolved key.
    resolved = str(Path("/vault/note.md").resolve())
    assert resolved not in store.store


def test_remove_note_deletes_entry(tmp_path):
    """remove_note explicitly deletes the entry."""
    getter = _fake_getter_factory()
    store = _make_store(tmp_path, getter)
    store.update_note("/vault/note.md", trigger_phrases=["t"], inhibitor_phrases=[])
    store.remove_note("/vault/note.md")
    resolved = str(Path("/vault/note.md").resolve())
    assert resolved not in store.store


# ── Persistence ──────────────────────────────────────────────────────────


def test_persistence_round_trip(tmp_path):
    """Save → load preserves trigger/inhibitor phrase texts."""
    getter = _fake_getter_factory()
    path = tmp_path / "trigger_store.json"
    store1 = TriggerStore(state_path=path, embedding_getter=getter)
    store1.update_note(
        "/vault/note.md",
        trigger_phrases=["when checking syntax"],
        inhibitor_phrases=["when the user just wants a summary"],
    )
    # New store loads from the same path.
    store2 = TriggerStore(state_path=path, embedding_getter=getter)
    resolved = str(Path("/vault/note.md").resolve())
    assert resolved in store2.store
    entry = store2.store[resolved]
    assert entry["trigger_phrases"] == ["when checking syntax"]
    assert entry["inhibitor_phrases"] == ["when the user just wants a summary"]
    assert len(entry["trigger_embs"]) == 1
    assert len(entry["inhibitor_embs"]) == 1


def test_no_getter_stores_phrases_only(tmp_path):
    """When no embedding getter is wired, phrases are stored but gate is inert."""
    store = TriggerStore(state_path=tmp_path / "ts.json", embedding_getter=None)
    store.update_note(
        "/vault/note.md",
        trigger_phrases=["some trigger"],
        inhibitor_phrases=["some inhibitor"],
    )
    resolved = str(Path("/vault/note.md").resolve())
    assert resolved in store.store
    assert store.store[resolved]["trigger_embs"] == []
    # Gate is inert (no embeddings) → passthrough.
    q = np.ones(8, dtype=np.float32)
    q = q / np.linalg.norm(q)
    should_drop, _, _ = store.check(q, "/vault/note.md", margin=0.05)
    assert not should_drop
