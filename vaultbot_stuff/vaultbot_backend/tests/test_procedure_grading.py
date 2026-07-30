"""Tests for the procedure grading loop: tracker + drift integration.

Covers log_step_result -> get_step_stats -> check_promotion flow,
run_promotion_cycle writing frontmatter, and the drift feedback
recorded for a passed procedure moving the note's embedding toward
the query.  No Ollama; uses tmp_path for isolation.

See [[Procedure-Subprocess-Architecture]] grading-loop section.
"""
import numpy as np
from pathlib import Path

from embedding_drift import EmbeddingDrift
from procedure_tracker import ProcedureTracker


def _dist(a, b):
    return float(np.linalg.norm(np.asarray(a, dtype=np.float32)
                                - np.asarray(b, dtype=np.float32)))


# ── Tracker log + stats + promotion ─────────────────────────────────────

def test_log_step_result_then_stats(tmp_path):
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"), vault_path=str(tmp_path))
    for _ in range(3):
        tracker.log_step_result("Proc-A", 1, passed=True, error="")
    stats = tracker.get_step_stats("Proc-A", 1)
    assert stats["total"] == 3
    assert stats["passes"] == 3
    assert stats["success_rate"] == 1.0


def test_check_promotion_verified_after_threshold(tmp_path):
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"), vault_path=str(tmp_path))
    # Log 5 passes at the procedure level (not step level).
    for _ in range(5):
        tracker.log_result("Proc-B", "task", "pass", "step_gate", "", "validation_error")
    assert tracker.check_promotion("Proc-B") == "verified"


def test_check_promotion_flagged_after_failures(tmp_path):
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"), vault_path=str(tmp_path))
    for _ in range(5):
        tracker.log_result("Proc-C", "task", "fail", "step_gate", "err", "validation_error")
    # 5 fails → success_rate 0 < 0.4 → flagged.
    assert tracker.check_promotion("Proc-C") == "flagged"


def test_check_promotion_needs_more_data(tmp_path):
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"), vault_path=str(tmp_path))
    tracker.log_result("Proc-D", "task", "pass", "step_gate", "", "validation_error")
    assert tracker.check_promotion("Proc-D") is None


def test_run_promotion_cycle_writes_verified_status(tmp_path):
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"), vault_path=str(tmp_path))
    # Write a procedure note with type: procedure.
    note = tmp_path / "Proc-E.md"
    note.write_text(
        "---\ntype: procedure\nstatus: experimental\n---\n## Steps\n1. do thing",
        encoding="utf-8")
    # 5 passes → verified.
    for _ in range(5):
        tracker.log_result("Proc-E", "task", "pass", "step_gate", "", "validation_error")
    result = tracker.run_promotion_cycle(str(tmp_path))
    assert "Proc-E" in result["promoted"]
    text = note.read_text(encoding="utf-8")
    assert "status: verified" in text


# ── Drift integration: procedure outcome → embedding scooch ─────────────

def test_drift_helpful_moves_toward_query(tmp_path):
    """A passed procedure should drift its embedding TOWARD the query."""
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    # Orthogonal unit vectors; initial distance is sqrt(2).
    content = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    query = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    base_dist = _dist(content, query)
    ed.record_feedback("proc.md", query, helpful=True)
    drifted = ed.apply_drift("proc.md", content)
    new_dist = _dist(drifted, query)
    assert new_dist < base_dist


def test_drift_unhelpful_moves_away_from_query(tmp_path):
    """A failed procedure should drift its embedding AWAY from the query."""
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    content = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    query = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    base_dist = _dist(content, query)
    ed.record_feedback("proc.md", query, helpful=False)
    drifted = ed.apply_drift("proc.md", content)
    new_dist = _dist(drifted, query)
    assert new_dist > base_dist


def test_drift_reset_on_content_change(tmp_path):
    """After reset, drift is gone — embedding returns to content vector."""
    ed = EmbeddingDrift(state_path=tmp_path / "drift.json", embedding_dim=4)
    content = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    query = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    ed.record_feedback("proc.md", query, helpful=True)
    assert ed.apply_drift("proc.md", content).tolist() != content.tolist()
    ed.reset("proc.md")
    assert ed.apply_drift("proc.md", content).tolist() == content.tolist()


# ── get_procedure_index (Phase 1 stem index) ────────────────────────────

def test_get_procedure_index_builds_stem_map(tmp_path):
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"), vault_path=str(tmp_path))
    note = tmp_path / "Proc-F.md"
    note.write_text(
        "---\ntype: procedure\nstatus: verified\n---\n## Steps\n1. do thing",
        encoding="utf-8")
    idx = tracker.get_procedure_index(str(tmp_path))
    assert "Proc-F" in idx
    assert idx["Proc-F"]["frontmatter"]["status"] == "verified"
    assert Path(idx["Proc-F"]["path"]).exists()