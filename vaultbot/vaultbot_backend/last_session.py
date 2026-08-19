"""Last-active session pointer — survives reconnects and restarts.

THE PROBLEM THIS SOLVES
-----------------------
Before this module, every WebSocket reconnect minted a fresh UUID and
the per-session state files (conversation_state_<old_uuid>.json,
working_memory_state_<old_uuid>.json) became unreachable — the bot woke
up with zero history. The frontend never sent the session_id back, and
the restart-adopt heuristic fell back to "most-recently-modified file by
filesystem mtime," which with 60+ accumulated files frequently picked the
WRONG session (a different tab's plan, or a previously-adopted file with
a fresh stamp).

THE FIX
-------
A single tiny JSON file, ``session_state/last_active_session.json``,
holds the session_id of the session that most recently processed a chat
turn. On a reconnect or restart, the WebSocket endpoint reads this
pointer and reuses that session_id (when the frontend doesn't send one
explicitly). This is the deterministic replacement for the fragile
"most-recent file by mtime" guess.

The pointer is written on every persisted turn (chat_handler calls
``touch`` after save_history) and on every WebSocket connect. It is
cleared on explicit ``/new``. Best-effort: never raises.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSIONS_DIR = Path(__file__).with_name("session_state")
_POINTER_PATH = _SESSIONS_DIR / "last_active_session.json"
_write_lock = threading.Lock()


def touch(session_id: str, title: str | None = None) -> None:
    """Record ``session_id`` as the most recently active session.

    Called after every persisted chat turn and on every WS connect so the
    pointer always reflects the session the operator is actually using.
    Best-effort: never raises.
    """
    if not session_id:
        return
    payload = {"session_id": session_id}
    if title:
        payload["title"] = title
    try:
        with _write_lock:
            _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(_SESSIONS_DIR), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False))
                os.replace(tmp, str(_POINTER_PATH))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("last_session.touch failed: %s", exc)


def read() -> str | None:
    """Return the last active session_id, or ``None`` if no pointer exists.

    Best-effort: on any failure returns ``None`` (caller mints a new UUID).
    """
    try:
        if not _POINTER_PATH.exists():
            return None
        with open(_POINTER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        sid = data.get("session_id") if isinstance(data, dict) else None
        return sid if isinstance(sid, str) and sid else None
    except Exception:  # noqa: BLE001 — best-effort
        return None


def read_title() -> str | None:
    """Return the last active session title, or ``None``."""
    try:
        if not _POINTER_PATH.exists():
            return None
        with open(_POINTER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        title = data.get("title") if isinstance(data, dict) else None
        return title if isinstance(title, str) and title else None
    except Exception:  # noqa: BLE001
        return None


def clear() -> None:
    """Remove the pointer (called on explicit ``/new``)."""
    try:
        with _write_lock:
            if _POINTER_PATH.exists():
                os.remove(str(_POINTER_PATH))
    except Exception:  # noqa: BLE001 — best-effort
        pass
