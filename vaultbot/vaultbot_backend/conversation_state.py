"""Persistent conversation state — survives backend restarts.

THE PROBLEM THIS SOLVES
-----------------------
``websocket.conversation_history`` lived only in RAM and was reset to ``[]``
on every new WebSocket connection (``routers/ws.py``). When the backend
restarted (the operator asked VaultBot to restart itself, or a crash, or a code
reload), the live conversation thread vanished. The agent woke up with
only the slow identity file (IDENTITY.md, which may point at
a goal from "a while ago") and zero recollection of the thread it was just
working on. the operator's words: "you totally wiped yourself bro rookie mistake,
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
  file doesn't grow unbounded across months. The in-memory sliding window
  bounds the LLM payload; this is a second bound for the disk copy.
- ``clear()`` wipes it (called on ``/new``).
- All operations are best-effort: a persistence failure must NEVER crash
  the chat loop. Errors are logged and swallowed.

The persisted shape is the same list of message dicts the chat loop already
uses (role/content/thinking/tool_calls). The system prompt is rebuilt fresh
each turn so it is NOT persisted — only the user/assistant/tool turns.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# How many recent turns to keep on disk. The in-memory compactor trims at
# 40 messages / 12k tokens (post-2026-07-28 hardening); this is a hard ceiling
# for the disk copy so a vault that runs for months doesn't accumulate a
# multi-MB conversation file. Each turn is a few hundred chars to a few KB,
# so 40 turns ≈ 50-150 KB.
MAX_TURNS = 40

# Hard char cap for the disk copy. Even 40 messages can total several MB
# if each is a large tool result (a full code_read or vault_read_note).
# Default raised to 2MB (2026-08-06) so tool results survive persistence
# instead of being chopped to 200-char stubs the model can't read.  A
# days-long session can still hit this cap; when it does, the oldest
# messages get their content field truncated to 200 chars (the truncation
# is per-message-oldest-first, not a file-wide chop). Configurable via env.
MAX_DISK_CHARS = int(os.getenv("VAULTBOT_HISTORY_MAX_CHARS", "2000000"))

_DEFAULT_PATH = str(Path(__file__).with_name("conversation_state.json"))
# Directory for per-session state files (parallel to the single legacy file).
_SESSIONS_DIR = Path(__file__).with_name("session_state")

# Serialize writes — the chat loop and any background thread never race.
_write_lock = threading.Lock()


def _session_path(session_id: str) -> str:
    """Return the per-session conversation state file path.

    Per-session files live in ``session_state/`` so multiple concurrent
    tabs don't stomp each other's persisted conversation.  The legacy
    bare ``conversation_state.json`` is migrated on first connect (see
    :func:`load_history`).
    """
    return str(_SESSIONS_DIR / f"conversation_state_{session_id}.json")


def _resolve_path(path: str | None, session_id: str | None = None) -> str:
    """Resolve the state file path.

    Priority: explicit ``path`` > per-session file > legacy default.
    When ``session_id`` is given the per-session file under
    ``session_state/`` is used, enabling multi-tab isolation.  When neither
    is provided the legacy single-file path is returned (back-compat for
    tests and callers that have not been updated).
    """
    if path:
        return path
    if session_id:
        return _session_path(session_id)
    return _DEFAULT_PATH


def load_history(
    path: str | None = None, session_id: str | None = None
) -> list[dict[str, Any]]:
    """Load the persisted conversation history.

    Returns ``[]`` when the file doesn't exist yet (first run — no history
    is correct, not an error). Raises ``ValueError`` when the file exists
    but is corrupt (JSON parse failure, wrong structure) — the caller
    should catch this and call ``notify_problem`` with the
    ``history_lost`` category so the user knows their conversation was
    lost, rather than silently starting fresh with no indication.

    When ``session_id`` is given the per-session file under
    ``session_state/`` is used.  If that file doesn't exist yet but the
    legacy single-file ``conversation_state.json`` does, a one-shot
    migration imports the legacy history and deletes the original so
    subsequent sessions start fresh (first-tab-wins).
    """
    p = _resolve_path(path, session_id)
    # One-shot legacy migration: if the per-session file doesn't exist
    # but the legacy single-file does, adopt it for this session.
    if (
        session_id
        and not path
        and not os.path.exists(p)
        and os.path.exists(_DEFAULT_PATH)
    ):
        try:
            legacy = _load_file(_DEFAULT_PATH)
            # Persist a copy under the session-specific path.
            _save_file(legacy, p)
            # Remove the legacy file so the next session starts fresh.
            with contextlib.suppress(OSError):
                os.remove(_DEFAULT_PATH)
            logger.info("conversation_state: migrated legacy history to %s", p)
            return legacy
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning("conversation_state: legacy migration failed: %s", exc)
    if not os.path.exists(p):
        return []  # first run — no history is correct
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(
                f"conversation_state: history file is not a list "
                f"({type(data).__name__}): {p}"
            )
        # Defensive: each entry must be a dict with a role.
        clean = [m for m in data if isinstance(m, dict) and m.get("role")]
        return clean
    except (json.JSONDecodeError, ValueError) as exc:
        # Re-raise with context so the caller can notify the user.
        # Auto-backup the corrupt file before raising so the user's
        # data isn't lost if they want to try manual recovery.
        try:
            import shutil

            backup = f"{p}.corrupt.{int(time.time())}"
            shutil.copy2(p, backup)
            logger.warning(
                "conversation_state: corrupt history backed up to %s: %s", backup, exc
            )
        except OSError:
            pass  # backup failure shouldn't mask the original error
        raise ValueError(
            f"Conversation history is corrupt and can't be loaded: {exc}. "
            f"A backup was saved. Starting fresh."
        ) from exc


def _load_file(p: str) -> list[dict[str, Any]]:
    """Load and validate a conversation history file."""
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(
            f"conversation_state: history file is not a list "
            f"({type(data).__name__}): {p}"
        )
    # Defensive: each entry must be a dict with a role.
    return [m for m in data if isinstance(m, dict) and m.get("role")]


def _save_file(history: list[dict[str, Any]], p: str) -> None:
    """Write history to path (atomic, locked). Never raises."""
    try:
        payload = json.dumps(history, ensure_ascii=False, default=str)
    except Exception as exc:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
            except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
    except Exception as exc:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        logger.warning("conversation_state save failed: %s", exc)


def save_history(
    history: list[dict[str, Any]],
    path: str | None = None,
    session_id: str | None = None,
) -> None:
    """Persist the conversation history to disk (atomic, bounded, locked).

    Best-effort: never raises. Truncates to the last ``MAX_TURNS`` messages
    so the file stays bounded across months of use.
    """
    if not isinstance(history, list):
        return
    p = _resolve_path(path, session_id)
    # Bound the disk copy: keep the most recent MAX_TURNS messages.
    bounded = history[-MAX_TURNS:] if len(history) > MAX_TURNS else history
    # Hard char cap: if the bounded slice is still too large (e.g. 40
    # messages each with a 50K-char tool result), truncate the oldest
    # messages' content until the total fits. This guarantees the disk
    # file never exceeds ~MAX_DISK_CHARS, so a restart after a days-long
    # session loads instantly instead of deserializing a multi-MB blob.
    total_chars = sum(
        len(str(m.get("content", "") or "")) + len(str(m.get("thinking", "") or ""))
        for m in bounded
        if isinstance(m, dict)
    )
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
                        total_chars -= old_len - len(str(m[key]))
    _save_file(bounded, p)


def clear_history(path: str | None = None, session_id: str | None = None) -> None:
    """Wipe the persisted history (called on ``/new``). Best-effort."""
    p = _resolve_path(path, session_id)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception as exc:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        logger.warning("conversation_state clear failed: %s", exc)


def clear_trail_tracker(vault_path: str | None = None) -> None:
    """Wipe the conversation trail tracker (called on ``/new``).

    Deletes ``Memory/_last_chat_note.txt`` so the next chat note starts a
    fresh trail instead of linking to the pre-reset conversation.
    Best-effort: never raises.
    """
    try:
        vp = Path(vault_path or os.getenv("VAULT_PATH", "."))
        tracker = vp / "vaultbot" / "Memory" / "_last_chat_note.txt"
        if tracker.exists():
            tracker.unlink()
    except Exception as exc:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        logger.warning("trail tracker clear failed: %s", exc)
