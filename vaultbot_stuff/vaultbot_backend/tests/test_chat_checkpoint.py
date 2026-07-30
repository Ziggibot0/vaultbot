"""Tests for chat-loop checkpoint/resume (chat_checkpoint.py) and the
TaskList.restore_snapshot it relies on.

Offline: no FAISS, no Ollama, no network. Uses tmp_path for the state file.

Leaf-module imports only — `import main` is hard-fenced by conftest.py.
"""
from __future__ import annotations

import time

from chat_checkpoint import ChatLoopCheckpointer, snapshot_working_memory
from working_memory import TaskList


# ---------------------------------------------------------------------------
# ChatLoopCheckpointer
# ---------------------------------------------------------------------------
def test_save_then_load_roundtrip(tmp_path):
    cp = ChatLoopCheckpointer(state_path=tmp_path / "cp.json")
    cp.save({
        "user_message": "research X",
        "round_idx": 3,
        "tool_history": [{"round": 0, "tool": "vault_search", "result_summary": "5 hits"}],
        "working_memory": {"goal": "g", "tasks": []},
    })
    loaded = cp.load()
    assert loaded is not None
    assert loaded["user_message"] == "research X"
    assert loaded["round_idx"] == 3
    assert loaded["tool_history"][0]["tool"] == "vault_search"


def test_load_returns_none_when_absent(tmp_path):
    cp = ChatLoopCheckpointer(state_path=tmp_path / "nope.json")
    assert cp.load() is None


def test_load_ignores_stale_checkpoint(tmp_path):
    cp = ChatLoopCheckpointer(state_path=tmp_path / "cp.json")
    cp.save({"user_message": "old turn", "round_idx": 1})
    # Force the stored timestamp into the past so it's stale.
    import json
    data = json.loads((tmp_path / "cp.json").read_text(encoding="utf-8"))
    data["_ts"] = time.time() - 100000
    (tmp_path / "cp.json").write_text(json.dumps(data), encoding="utf-8")
    assert cp.load(max_age_s=7200) is None


def test_clear_removes_checkpoint(tmp_path):
    cp = ChatLoopCheckpointer(state_path=tmp_path / "cp.json")
    cp.save({"user_message": "x", "round_idx": 0})
    assert (tmp_path / "cp.json").exists()
    cp.clear()
    assert not (tmp_path / "cp.json").exists()
    assert cp.load() is None


def test_load_rejects_corrupt_file(tmp_path):
    p = tmp_path / "cp.json"
    p.write_text("{ not valid json", encoding="utf-8")
    cp = ChatLoopCheckpointer(state_path=p)
    assert cp.load() is None


# ---------------------------------------------------------------------------
# TaskList.restore_snapshot
# ---------------------------------------------------------------------------
def test_restore_snapshot_roundtrip():
    wm = TaskList()
    wm.set_plan("do the thing", ["step one", "step two"])
    wm.update_task("1", status="completed", notes="done")
    snap = wm.snapshot()

    wm2 = TaskList()
    wm2.restore_snapshot(snap)
    assert wm2.goal == "do the thing"
    assert len(wm2.tasks) == 2
    assert wm2.tasks[0].status == "completed"
    assert wm2.tasks[0].notes == "done"
    assert wm2.tasks[1].status == "pending"


def test_restore_snapshot_skips_malformed():
    wm = TaskList()
    wm.restore_snapshot({"goal": "g", "tasks": [{"content": "ok", "status": "bogus"}, "not-a-dict"]})
    # bogus status coerced to pending; non-dict skipped
    assert len(wm.tasks) == 1
    assert wm.tasks[0].status == "pending"


def test_snapshot_working_memory_helper():
    wm = TaskList()
    wm.set_plan("g", ["a"])
    snap = snapshot_working_memory(wm)
    assert isinstance(snap, dict)
    assert snap["goal"] == "g"
    # A broken object yields {} instead of raising.
    assert snapshot_working_memory(object()) == {}
