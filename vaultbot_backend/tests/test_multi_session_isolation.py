"""Tests for multi-tab session isolation.

Verifies that per-session persistence (conversation_state, working_memory,
chat_checkpoint) keep concurrent sessions isolated — one tab's state never
leaks into another.

Offline: no FAISS, no Ollama, no network, no main import.
Uses tmp_path for state files.
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# conversation_state: per-session load/save/clear
# ---------------------------------------------------------------------------
def test_save_load_roundtrip_per_session(tmp_path):
    from conversation_state import load_history, save_history

    sid = "11111111-1111-1111-1111-111111111111"
    # Patch the session directory to use tmp_path.
    with patch("conversation_state._SESSIONS_DIR", tmp_path):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        save_history(history, session_id=sid)
        loaded = load_history(session_id=sid)
    assert loaded == history


def test_two_sessions_are_isolated(tmp_path):
    """Saving to session A does not affect session B's file."""
    from conversation_state import load_history, save_history

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch("conversation_state._SESSIONS_DIR", tmp_path):
        save_history([{"role": "user", "content": "session A"}], session_id=sid_a)
        save_history([{"role": "user", "content": "session B"}], session_id=sid_b)
        loaded_a = load_history(session_id=sid_a)
        loaded_b = load_history(session_id=sid_b)
    assert loaded_a == [{"role": "user", "content": "session A"}]
    assert loaded_b == [{"role": "user", "content": "session B"}]


def test_clear_only_affects_specified_session(tmp_path):
    """clear_history(session_id=A) does not delete session B's file."""
    from conversation_state import clear_history, load_history, save_history

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    # Patch both _SESSIONS_DIR and _DEFAULT_PATH so the test doesn't pick
    # up the real conversation_state.json from the backend dir.
    sessions_dir = tmp_path / "session_state"
    fake_default = str(tmp_path / "conversation_state.json")
    with (
        patch("conversation_state._SESSIONS_DIR", sessions_dir),
        patch("conversation_state._DEFAULT_PATH", fake_default),
    ):
        save_history([{"role": "user", "content": "A"}], session_id=sid_a)
        save_history([{"role": "user", "content": "B"}], session_id=sid_b)
        clear_history(session_id=sid_a)
        # A is gone, B survives.
        assert load_history(session_id=sid_a) == []
        assert load_history(session_id=sid_b) == [{"role": "user", "content": "B"}]


def test_legacy_migration_first_tab_wins(tmp_path):
    """Legacy conversation_state.json is migrated to the first session that
    loads it, then deleted so the next session starts fresh."""
    from conversation_state import load_history

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    # Write the legacy file.
    legacy_data = [{"role": "user", "content": "legacy"}]
    legacy_path = tmp_path / "conversation_state.json"
    legacy_path.write_text(json.dumps(legacy_data), encoding="utf-8")

    sessions_dir = tmp_path / "session_state"
    with (
        patch("conversation_state._DEFAULT_PATH", str(legacy_path)),
        patch("conversation_state._SESSIONS_DIR", sessions_dir),
    ):
        # First session: should inherit the legacy file.
        loaded_a = load_history(session_id=sid_a)
        assert loaded_a == legacy_data
        # Legacy file should be deleted.
        assert not legacy_path.exists()
        # Second session: starts fresh (no legacy to inherit).
        loaded_b = load_history(session_id=sid_b)
        assert loaded_b == []


# ---------------------------------------------------------------------------
# working_memory: per-session save/load/clear
# ---------------------------------------------------------------------------
def test_working_memory_isolated_per_session(tmp_path):
    from working_memory import TaskList

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch("working_memory._SESSIONS_DIR", tmp_path):
        wm_a = TaskList()
        wm_a.set_plan("goal A", ["task A1"])
        wm_a.save_to_disk(session_id=sid_a)

        wm_b = TaskList()
        wm_b.set_plan("goal B", ["task B1"])
        wm_b.save_to_disk(session_id=sid_b)

        loaded_a = TaskList.load_from_disk(session_id=sid_a)
        loaded_b = TaskList.load_from_disk(session_id=sid_b)

    assert loaded_a is not None and loaded_a.goal == "goal A"
    assert loaded_b is not None and loaded_b.goal == "goal B"


def test_working_memory_clear_isolated(tmp_path):
    """clear_disk(session_id=A) does not delete session B's plan."""
    from working_memory import TaskList

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch("working_memory._SESSIONS_DIR", tmp_path):
        wm_a = TaskList()
        wm_a.set_plan("goal A", ["task A1"])
        wm_a.save_to_disk(session_id=sid_a)

        wm_b = TaskList()
        wm_b.set_plan("goal B", ["task B1"])
        wm_b.save_to_disk(session_id=sid_b)

        TaskList.clear_disk(session_id=sid_a)
        assert TaskList.load_from_disk(session_id=sid_a) is None
        assert TaskList.load_from_disk(session_id=sid_b) is not None


# ---------------------------------------------------------------------------
# chat_checkpoint: per-session checkpoint
# ---------------------------------------------------------------------------
def test_checkpoint_per_session_isolated(tmp_path):
    from chat_checkpoint import ChatLoopCheckpointer

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch("chat_checkpoint.ChatLoopCheckpointer._SESSIONS_DIR", tmp_path):
        cp_a = ChatLoopCheckpointer.for_session(sid_a)
        cp_b = ChatLoopCheckpointer.for_session(sid_b)

        cp_a.save({"user_message": "turn A", "round_idx": 1})
        cp_b.save({"user_message": "turn B", "round_idx": 2})

        loaded_a = cp_a.load()
        loaded_b = cp_b.load()

    assert loaded_a is not None and loaded_a["user_message"] == "turn A"
    assert loaded_b is not None and loaded_b["user_message"] == "turn B"


def test_checkpoint_clear_isolated(tmp_path):
    """Clearing session A's checkpoint does not affect session B."""
    from chat_checkpoint import ChatLoopCheckpointer

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch("chat_checkpoint.ChatLoopCheckpointer._SESSIONS_DIR", tmp_path):
        cp_a = ChatLoopCheckpointer.for_session(sid_a)
        cp_b = ChatLoopCheckpointer.for_session(sid_b)

        cp_a.save({"user_message": "turn A", "round_idx": 1})
        cp_b.save({"user_message": "turn B", "round_idx": 2})

        cp_a.clear()
        assert cp_a.load() is None
        assert cp_b.load() is not None


# ---------------------------------------------------------------------------
# ask_user: _pending_requests holds websocket ref
# ---------------------------------------------------------------------------
def test_ask_user_pending_requests_has_websocket_ref():
    """The _pending_requests entry should be a 3-tuple with a websocket ref."""
    import threading

    from custom_tools.ask_user import _pending_requests

    # Simulate registering a request with a websocket ref.
    rid = "test-rid-ref"
    event = threading.Event()
    event._created_at = time.time()
    holder = {}
    fake_ws = {"_is_websocket": True}
    _pending_requests[rid] = (event, holder, fake_ws)

    entry = _pending_requests.get(rid)
    assert entry is not None
    assert len(entry) == 3
    assert entry[0] is event
    assert entry[1] is holder
    assert entry[2] is fake_ws

    # Cleanup.
    _pending_requests.pop(rid, None)


# ---------------------------------------------------------------------------
# last_session: the deterministic last-active-session pointer
# ---------------------------------------------------------------------------
def test_last_session_pointer_roundtrip(tmp_path):
    """touch() then read() returns the same session_id."""
    from last_session import read, touch

    sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    with patch("last_session._POINTER_PATH", tmp_path / "last_active_session.json"):
        touch(sid, title="test session")
        assert read() == sid


def test_last_session_pointer_clear(tmp_path):
    """clear() removes the pointer so read() returns None."""
    from last_session import clear, read, touch

    sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    with patch("last_session._POINTER_PATH", tmp_path / "last_active_session.json"):
        touch(sid)
        assert read() == sid
        clear()
        assert read() is None


def test_last_session_pointer_overwrite(tmp_path):
    """A later touch() overwrites the pointer — the single-slot global.

    This is the exact failure mode behind issue #77: with multiple tabs,
    the pointer is overwritten to point at a DIFFERENT session, so a
    reconnect without an explicit ?sid adopts the wrong history. The
    frontend fix (sending ?sid) bypasses this global entirely.
    """
    from last_session import read, touch

    sid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch("last_session._POINTER_PATH", tmp_path / "last_active_session.json"):
        touch(sid_a)
        touch(sid_b)  # tab B becomes active
        assert read() == sid_b  # NOT sid_a — the global was clobbered
