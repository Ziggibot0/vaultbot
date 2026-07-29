"""Persistent conversation state — survives backend restarts.

THE PROBLEM THIS SOLVES
-----------------------
``websocket.conversation_history`` lived only in RAM and was reset to ``[]``
on every new WebSocket connection (``routers/ws.py``). When the backend
restarted (Sean asked VaultBot to restart itself, or a crash, or a code
reload), the live conversation thread vanished. The agent woke up with
only the slow identity files (IDENTITY/SELF_MODEL/GOALS, which may point at
a goal from "a while ago") and zero recollection of the thread it was just
working on. Sean's words: "you totally wiped yourself bro rookie mistake,
change your restart backend tool to bring you back into the same session
and start you back up."

THE FIX
-------
Persist the conversation history to a single JSON file on disk after every
turn, and reload it on the next WebSocket connect. The agent now "wakes up
in the same session" after a restart — the live thread is restored, not
just the slow identity layer.

DESIGN
------
- One file: ``conversation_state.json`` in the backend dir.
- Atomic write (temp + os.replace) with a lock — the chat loop and any
  background thread never tear the file.
- Bounded: only the last ``MAX_TURNS`` messages are kept on disk so the
  file doesn't grow unbounded across months. The in-memory compactor
  already trims at 80 messages / 20k tokens; this is a second bound for
  the disk copy.
- ``clear()`` wipes it (called on ``/new``).
- All operations are best-effort: a persistence failure must NEVER crash
  the chat loop. Errors are logged and swallowed.

The persisted shape is the same list of message dicts the chat loop already
uses (role/content/thinking/tool_calls). The system prompt is rebuilt fresh
each turn so it is NOT persisted — only the user/assistant/tool turns.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# How many recent turns to keep on disk. The in-memory compactor trims at
# 40 messages / 12k tokens (post-2026-07-28 hardening); this is a hard ceiling
# for the disk copy so a vault that runs for months doesn't accumulate a
# multi-MB conversation file. Each turn is a few hundred chars to a few KB,
# so 40 turns ≈ 50-150 KB.
MAX_TURNS = 40

# Hard char cap for the disk copy. Even 40 messages can total 200K+ chars
# if each is a large tool result. Cap the file at ~100K chars by truncating
# the oldest messages' content when the total exceeds this. This is the
# disk-level guarantee that a days-long session doesn't produce a
# conversation_state.json that's too big to load on the next restart.
MAX_DISK_CHARS = int(os.getenv("VAULTBOT_HISTORY_MAX_CHARS", "100000"))

_DEFAULT_PATH = str(Path(__file__).with_name("conversation_state.json"))

# Serialize writes — the chat loop and any background thread never race.
_write_lock = threading.Lock()


def _resolve_path(path: str | None) -> str:
    return path if path else _DEFAULT_PATH


def load_history(path: str | None = None) -> list[dict[str, Any]]:
    """Load the persisted conversation history. Returns [] on any failure
    (missing file, corrupt JSON, etc.) — never raises."""
    p = _resolve_path(path)
    try:
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            logger.warning("conversation_state: not a list, ignoring")
            return []
        # Defensive: each entry must be a dict with a role.
        clean = [m for m in data
                 if isinstance(m, dict) and m.get("role")]
        return clean
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_state load failed: %s", exc)
        return []


def save_history(history: list[dict[str, Any]],
                 path: str | None = None) -> None:
    """Persist the conversation history to disk (atomic, bounded, locked).

    Best-effort: never raises. Truncates to the last ``MAX_TURNS`` messages
    so the file stays bounded across months of use.
    """
    if not isinstance(history, list):
        return
    p = _resolve_path(path)
    # Bound the disk copy: keep the most recent MAX_TURNS messages.
    bounded = history[-MAX_TURNS:] if len(history) > MAX_TURNS else history
    # Hard char cap: if the bounded slice is still too large (e.g. 40
    # messages each with a 50K-char tool result), truncate the oldest
    # messages' content until the total fits. This guarantees the disk
    # file never exceeds ~MAX_DISK_CHARS, so a restart after a days-long
    # session loads instantly instead of deserializing a multi-MB blob.
    total_chars = sum(len(str(m.get("content", "") or "")) +
                      len(str(m.get("thinking", "") or ""))
                      for m in bounded if isinstance(m, dict))
    if total_chars > MAX_DISK_CHARS:
        bounded = list(bounded)  # copy so we can mutate
        # Truncate from the oldest message forward.
        for i in range(len(bounded)):
            if total_chars <= MAX_DISK_CHARS:
                break
            m = bounded[i]
            if isinstance(m, dict):
                for key in ("content", "thinking"):
                    val = m.get(key, "")
                    if val and len(str(val)) > 200:
                        old_len = len(str(val))
                        m[key] = str(val)[:200] + "\n[...truncated on persist...]"
                        total_chars -= (old_len - len(str(m[key])))
    # Strip any non-serializable leftovers defensively.
    try:
        payload = json.dumps(bounded, ensure_ascii=False, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_state serialize failed: %s", exc)
        return
    try:
        with _write_lock:
            d = Path(p).parent
            d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(d), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, p)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_state save failed: %s", exc)


def clear_history(path: str | None = None) -> None:
    """Wipe the persisted history (called on ``/new``). Best-effort."""
    p = _resolve_path(path)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_state clear failed: %s", exc)