"""Tests for the embedding-drift state machine in embedding_drift.py.

Pure numpy math — no Ollama, no vault, no network. Each test gets a fresh
state JSON via `tmp_path` so drift state never leaks between tests.

Documentation grounding:
- tmp_path: unique pathlib.Path per test
  https://docs.pytest.org/en/stable/reference/reference.html
- monkeypatch: auto-undone after each test
  https://docs.pytest.org/en/stable/reference/reference.html
- Anatomy of a test (arrange/act/assert)
  https://docs.pytest.org/en/stable/explanation/anatomy.html
"""

import numpy as np
import pytest

pytestmark = pytest.mark.unit

from embedding_drift import DRIFT_MAX_RATIO, EmbeddingDrift


def _dist(a, b):
    return float(
        np.linalg.norm(
            np.asarray(a, dtype=np.float32) - np.asarray(b, dtype=np.float32)
        )
    )


def test_helpful_signal_reduces_query_distance(tmp_path):
    # Arrange: orthogonal unit vectors; initial distance is sqrt(2).
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    c = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    q = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    base_dist = _dist(c, q)
    assert abs(base_dist - np.sqrt(2.0)) < 1e-6

    # Act: record a helpful signal for "note.md", then apply drift to content.
    ed.record_feedback("note.md", q, helpful=True)
    drifted = ed.apply_drift("note.md", c)

    # Assert: drifted vector is now closer to the query than the raw content.
    assert _dist(drifted, q) < base_dist


def test_unhelpful_signal_increases_query_distance(tmp_path):
    # Arrange: same orthogonal setup.
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    c = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    q = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    base_dist = _dist(c, q)

    # Act: record an UNHELPFUL signal, nudging the drift away from the query.
    ed.record_feedback("note.md", q, helpful=False)
    drifted = ed.apply_drift("note.md", c)

    # Assert: drifted vector is now FARTHER from the query than raw content.
    assert _dist(drifted, q) > base_dist


def test_reset_on_rewrite_returns_to_content(tmp_path):
    # Arrange: accumulate several helpful signals so drift is non-trivial.
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    c = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    q = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    for _ in range(5):
        ed.record_feedback("note.md", q, helpful=True)
    drifted = ed.apply_drift("note.md", c)
    assert _dist(drifted, c) > 1e-6  # drift is non-zero before reset

    # Act: reset the note (simulating a rewrite).
    ed.reset("note.md")

    # Assert: apply_drift returns the pure content vector again.
    after = ed.apply_drift("note.md", c)
    assert float(np.max(np.abs(after - c))) < 1e-6


def test_drift_cap_holds_under_repeated_signals(tmp_path):
    # Arrange: content is a unit vector; DRIFT_MAX_RATIO caps drift at 0.3.
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    c = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    q = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-6

    # Act: hammer 100 helpful signals with the same query.
    for _ in range(100):
        ed.record_feedback("note.md", q, helpful=True)
    drifted = ed.apply_drift("note.md", c)

    # Assert: drift magnitude never exceeds DRIFT_MAX_RATIO * ||c||.
    drift_norm = float(np.linalg.norm(drifted - c))
    cap = DRIFT_MAX_RATIO * float(np.linalg.norm(c))
    assert drift_norm <= cap + 1e-6
