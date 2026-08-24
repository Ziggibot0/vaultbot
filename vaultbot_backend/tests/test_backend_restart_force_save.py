"""Tests for the backend_restart live-conversation force-save.

Regression for session 1e7514e8: a long multi-round turn (146 live
conversation messages) called ``backend_restart`` mid-turn. The force-save
read ``websocket.conversation_history``, which is only synced to the live
``conversation`` list at the END of a turn — so it persisted only the stale
pre-turn history (7 turns) and the agent woke up after the restart with a
truncated thread (memory loss + truncated output).

The fix threads the LIVE ``conversation`` list down to
``execute_agent_tool`` so the ``backend_restart`` force-save persists the
actual current thread. These tests verify that:
  1. When a live ``conversation`` is passed, it is what gets persisted
     (not the stale ``websocket.conversation_history``).
  2. When no live conversation is passed, the websocket copy is used as a
     fallback (back-compat for non-loop callers).
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


class _FakeSessionLogger:
    """Collects log events so the test can assert on them."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, data: dict | None = None):
        self.events.append((event, data or {}))

    def log_exception(self, exc, context=""):
        self.events.append(("exception", {"context": context, "error": str(exc)}))

    def next_call_id(self):
        return len(self.events) + 1


def _make_svc(tmp_path: Path):
    """A fake Services with a self_improver that reports backend_restart."""
    improver = SimpleNamespace(
        has_tool=lambda name: name == "backend_restart",
        execute_custom_tool=lambda name, args: {"status": "restart_requested"},
    )
    return SimpleNamespace(self_improver=improver)


def _make_websocket(tmp_path: Path, session_id: str, stale_history: list):
    """A fake websocket with a stale conversation_history and a session_id."""
    return SimpleNamespace(
        session_id=session_id,
        conversation_history=stale_history,
        working_memory=None,
    )


def _run(svc, websocket, tool_name, args, logger, conversation=None):
    """Call execute_agent_tool in a fresh event loop."""
    import importlib

    # backend_restart is a dangerous tool blocked by Safe Mode (default ON).
    # Disable Safe Mode so the force-save branch actually runs.
    import os

    os.environ["VAULTBOT_SAFE_MODE"] = "0"
    import safe_mode

    importlib.reload(safe_mode)

    from chat_tool_dispatch import execute_agent_tool

    return asyncio.run(
        execute_agent_tool(
            svc,
            tool_name,
            args,
            logger,
            websocket,
            user_message="test",
            conversation=conversation,
        )
    )


def _load_persisted(tmp_path: Path, session_id: str) -> list:
    p = tmp_path / "session_state" / f"conversation_state_{session_id}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def test_force_save_uses_live_conversation_when_passed(tmp_path, monkeypatch):
    """The live conversation (146 msgs) must be persisted, not the stale 7."""
    import conversation_state as cs

    # Redirect conversation_state's session dir to tmp_path so the test
    # never touches a real backend session_state/.
    monkeypatch.setattr(cs, "_SESSIONS_DIR", tmp_path / "session_state")

    session_id = "test-session-1"
    stale = [{"role": "user", "content": f"stale-{i}"} for i in range(7)]
    live = [{"role": "user", "content": f"live-{i}"} for i in range(146)]

    svc = _make_svc(tmp_path)
    websocket = _make_websocket(tmp_path, session_id, stale)
    logger = _FakeSessionLogger()

    result = _run(svc, websocket, "backend_restart", {}, logger, conversation=live)

    assert result["status"] == "restart_requested"
    persisted = _load_persisted(tmp_path, session_id)
    # save_history bounds the disk copy to MAX_TURNS (40) — the important
    # thing is that it persisted the LIVE conversation (the last 40 of the
    # 146 live messages), NOT the stale 7-message websocket copy.
    assert len(persisted) == 40, (
        "force-save must persist the LIVE conversation (bounded to "
        f"MAX_TURNS=40), not the stale 7-message websocket copy "
        f"(got {len(persisted)})"
    )
    assert persisted[0]["content"] == "live-106"
    assert persisted[-1]["content"] == "live-145"
    assert all(m["content"].startswith("live-") for m in persisted), (
        "persisted messages must come from the LIVE conversation, "
        "not the stale websocket history"
    )

    # The log should record the live source.
    saved = [e for e in logger.events if e[0] == "conv_force_saved_before_restart"]
    assert saved, "expected conv_force_saved_before_restart log event"
    assert saved[0][1]["turns"] == 146
    assert saved[0][1]["source"] == "live_conversation"


def test_force_save_falls_back_to_websocket_history(tmp_path, monkeypatch):
    """Without a live conversation, the websocket copy is used (back-compat)."""
    import conversation_state as cs

    monkeypatch.setattr(cs, "_SESSIONS_DIR", tmp_path / "session_state")

    session_id = "test-session-2"
    stale = [{"role": "user", "content": f"stale-{i}"} for i in range(7)]

    svc = _make_svc(tmp_path)
    websocket = _make_websocket(tmp_path, session_id, stale)
    logger = _FakeSessionLogger()

    result = _run(svc, websocket, "backend_restart", {}, logger, conversation=None)

    assert result["status"] == "restart_requested"
    persisted = _load_persisted(tmp_path, session_id)
    assert len(persisted) == 7
    saved = [e for e in logger.events if e[0] == "conv_force_saved_before_restart"]
    assert saved
    assert saved[0][1]["source"] == "websocket_history"
