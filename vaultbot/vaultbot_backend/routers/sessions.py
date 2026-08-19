"""Session history endpoints, extracted from routers/system.py.

A lightweight listing of past chat sessions (one per .jsonl in sessions/)
so the sidebar's History disclosure can show "what would I lose if I
/new?" without the user having to find the sessions/ folder. Each entry
carries the session id, a human-readable start time, and a one-line
preview (the first user message) so the list is scannable. Read-only —
this never modifies or deletes session files.
"""

from __future__ import annotations

import json as _json
import re as _re
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

_SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


def _extract_session_preview(path: Path) -> dict[str, Any] | None:
    """Read the first + last lines of a session .jsonl for a list entry.

    Returns ``None`` if the file can't be parsed (corrupt/empty). Keeps the
    endpoint resilient: one bad session file never breaks the whole list.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None

    session_id = path.stem
    started_at = ""
    preview = ""
    # Scan the whole file for the session_start + first user message.
    # The user message ("in" event) can be hundreds of lines in (after
    # boot-time tool calls + init), so we can't just read the first 20.
    # We break early once we have both started_at + preview to stay fast.
    for line in lines:
        if started_at and preview:
            break
        try:
            evt = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        # New format: event-based (session_start, websocket_message, etc.)
        if not started_at and evt.get("event") == "session_start":
            started_at = evt.get("started_at", "")
            continue
        if not preview:
            data = evt.get("data") or {}
            if evt.get("event") == "websocket_message":
                d = data or {}
                if d.get("direction") == "in":
                    payload = d.get("payload") or {}
                    msg = payload.get("message") or ""
                    if msg:
                        preview = msg[:120]
            elif evt.get("event") == "chat_begin":
                msg = data.get("user_message") or ""
                if msg:
                    preview = msg[:120]
        # Old format: type-based (session_start, user_message, etc.)
        if not started_at and evt.get("type") == "session_start":
            ts = evt.get("timestamp", 0)
            started_at = str(ts) if ts else ""
            continue
        if not preview and evt.get("type") == "user_message":
            msg = evt.get("content") or ""
            if msg:
                preview = msg[:120]
    # Also look for a session_title event (set by the user or auto-generated).
    title = ""
    for line in lines:
        try:
            evt = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if evt.get("event") == "session_title":
            title = evt.get("title", "")
            break  # last one wins, but there's typically only one
    return {
        "session_id": session_id,
        "started_at": started_at,
        "preview": preview or "(no messages)",
        "title": title or (preview[:60] if preview else "New Session"),
    }


@router.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    """List recent chat sessions for the sidebar History disclosure.

    Returns ``{"sessions": [{"session_id", "started_at", "preview"}, ...]}``
    sorted newest-first. Reads the sessions/ directory; each .jsonl is one
    session. Only the first ~20 lines of each file are read (for the start
    time + first user message) so this stays fast even with hundreds of
    sessions. Corrupt files are silently skipped.
    """
    if not _SESSIONS_DIR.exists():
        return {"sessions": []}
    entries = []
    for f in _SESSIONS_DIR.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        preview = _extract_session_preview(f)
        if preview is None:
            continue
        preview["mtime"] = mtime
        entries.append(preview)
    # Sort newest-first by file mtime (more reliable than started_at string
    # parse, which can be empty for old sessions).
    entries.sort(key=lambda e: e.get("mtime", 0.0), reverse=True)
    # Drop the mtime from the response payload — it was just for sorting.
    for e in entries:
        e.pop("mtime", None)
    # Cap at 50 so the list stays scannable; the user almost never needs
    # older sessions in the sidebar.
    return {"sessions": entries[:50]}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Return the turns of one session, for read-only replay.

    Parses the .jsonl session log and extracts message turns in order.
    Supports three event formats:

    1. **Explicit assistant_response** (new): ``event: "assistant_response"``
       with ``data.text`` (or ``data.content``) — the most reliable source.
    2. **websocket_message** (current): ``event: "websocket_message"`` with
       ``data.direction`` in/out and ``data.payload.type`` answer_chunk /
       answer_done — accumulates chunks into the current assistant turn.
    3. **Old type-based format**: ``type: "user_message"`` / ``type:
       "assistant_response"`` with ``content`` / ``text`` fields.

    Also extracts ``tool_call`` and ``thinking`` events so the replay shows
    what VaultBot actually did, not just the final text.

    Returns ``{"turns": [{"role", "content", "tool_name"?, "thinking"?}]}``.
    Read-only — never modifies the session file or the live conversation.

    The ``session_id`` is validated to reject path separators so a crafted
    id can't escape the sessions/ directory.
    """
    # Validate: allow UUIDs (36 chars) and timestamp_id format (e.g.
    # "1752150184_9479"). Reject anything with path separators.
    if not _re.fullmatch(r"[0-9a-fA-F_-]{1,60}", session_id):
        return {"turns": [], "error": "invalid session id"}
    path = _SESSIONS_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return {"turns": [], "error": "session not found"}

    turns: list[dict[str, Any]] = []
    current_assistant = ""
    current_thinking = ""

    def _flush_assistant():
        nonlocal current_assistant, current_thinking
        if current_assistant:
            turn: dict[str, Any] = {"role": "assistant", "content": current_assistant}
            if current_thinking:
                turn["thinking"] = current_thinking
            turns.append(turn)
            current_assistant = ""
            current_thinking = ""

    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                evt = _json.loads(line)
            except _json.JSONDecodeError:
                continue

            # --- Format 3: old type-based format (type field at top level) ---
            evt_type = evt.get("type")
            if evt_type == "user_message":
                _flush_assistant()
                msg = evt.get("content") or ""
                if msg:
                    turns.append({"role": "user", "content": msg})
                continue
            if evt_type == "assistant_response":
                _flush_assistant()
                # Old format: content is often empty, text has the actual response
                text = evt.get("text") or evt.get("content") or ""
                if text:
                    turns.append({"role": "assistant", "content": text})
                continue
            if evt_type == "tool_call":
                tool_name = evt.get("tool_name") or evt.get("content") or "tool"
                turns.append(
                    {
                        "role": "tool_call",
                        "content": str(tool_name),
                        "tool_name": str(tool_name),
                    }
                )
                continue

            # --- Format 1 & 2: event-based format ---
            event_name = evt.get("event")
            if event_name == "assistant_response":
                # Explicit assistant_response event (new, most reliable).
                _flush_assistant()
                data = evt.get("data") or {}
                text = data.get("text") or data.get("content") or ""
                if text:
                    turns.append({"role": "assistant", "content": text})
                continue

            if event_name == "thinking":
                # Accumulate thinking text for the current assistant turn.
                data = evt.get("data") or {}
                chunk = data.get("content") or data.get("chunk") or ""
                if chunk:
                    current_thinking += chunk
                continue

            if event_name == "tool_call":
                data = evt.get("data") or {}
                tool_name = data.get("tool") or data.get("tool_name") or "tool"
                turns.append(
                    {
                        "role": "tool_call",
                        "content": str(tool_name),
                        "tool_name": str(tool_name),
                    }
                )
                continue

            if event_name != "websocket_message":
                continue

            # --- Format 2: websocket_message events ---
            data = evt.get("data") or {}
            payload = data.get("payload") or {}
            direction = data.get("direction")
            if direction == "in":
                _flush_assistant()
                msg = payload.get("message") or ""
                if msg:
                    turns.append({"role": "user", "content": msg})
            elif direction == "out":
                ptype = payload.get("type")
                if ptype == "answer_chunk":
                    current_assistant += payload.get("content") or ""
                elif ptype == "thinking":
                    current_thinking += payload.get("content") or ""
                elif ptype == "answer_done":
                    # answer_done has the final assembled text in content.
                    # Prefer it over accumulated chunks (chunks may be
                    # skipped by send_personal_message's logging filter).
                    done_content = payload.get("content") or ""
                    if done_content:
                        current_assistant = done_content
                    _flush_assistant()
                elif ptype == "tool_call":
                    tool_name = (
                        payload.get("tool_name") or payload.get("content") or "tool"
                    )
                    turns.append(
                        {
                            "role": "tool_call",
                            "content": str(tool_name),
                            "tool_name": str(tool_name),
                        }
                    )
                elif ptype == "tool_result":
                    tool_name = payload.get("tool") or "tool"
                    summary = payload.get("summary") or ""
                    if summary:
                        turns.append(
                            {
                                "role": "tool_result",
                                "content": str(summary)[:500],
                                "tool_name": str(tool_name),
                            }
                        )

        # Flush trailing assistant text if the session ended mid-stream.
        _flush_assistant()
    except OSError:
        return {"turns": [], "error": "could not read session"}
    return {"turns": turns}
