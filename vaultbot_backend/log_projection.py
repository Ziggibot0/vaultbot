"""Log projection — JSONL session log → vault .md event files.

THE PROBLEM THIS SOLVES
-----------------------
Session logs are JSONL in vaultbot_backend/sessions/ — machine-readable but
invisible to the vault.  Chat notes (Memory/Chat/Chat-*.md) are a separate,
LLM-generated distilled layer that clutters the vault and costs an LLM pass
per consolidation.  Two memory layers, neither fully indexed, no graph
connection between sessions and vault knowledge.

THE SOLUTION
------------
Each event in the session JSONL becomes its own .md file under
myvault/vaultbot-stuff/Memory/Logs/<session-uuid>/.  Every file is indexed
individually (the event is the retrieval unit, not the file), tagged with
objective provenance, and wikilinked into the existing vault graph.  The
chat-*.md system is eliminated — its function is absorbed by the tagged,
individually-indexed, wikilinked event files.

The JSONL log remains the canonical dev anchor.  The .md layer is a
projection of it — one consumer, never a second writer.  Rebuilding the
vault view = replaying the JSONL.

PROVENANCE TAGS (objective only)
--------------------------------
Tags record WHERE the entry came from — the mode of input.  Every tag is
derivable from a JSONL field.  No semantic classification, no model judgment.

  [user]        — user message
  [assistant]    — assistant response
  [tool:<name>]  — tool call + result paired by call_id
  [think]        — model thinking/reasoning trace

That's the full set.  No [stage], [route], [error], [decision] — those are
dev-facing JSONL events that stay in the log.  The vault only sees what
constitutes memory: what was said, what was done, what was thought.

WIKILINKS
---------
The projection creates wikilinks connecting event files to existing vault
notes.  This makes session events first-class graph nodes — the existing
fused_retrieval.py graph channel walks them for free.  Reuses the existing
weaving.link_outbound + weaving.existing_note_titles machinery so there's
no new linking code.

CONFLICTS AS SIGNALS
--------------------
When retrieval surfaces two events in nearby semantic space that disagree,
the bot does NOT try to resolve the conflict.  It presents both as
superimposed-true, offers to look into it, and moves on.  The superposition
collapses when new evidence arrives, not when the model guesses.  This is a
retrieval philosophy, not a subsystem — no detector to build.

DESIGN GOALS
------------
- Lightweight: no LLM calls, no embedding calls (the vault watcher indexes
  the new .md files automatically via vault_indexer's file watcher).
- One consumer: the projection reads JSONL and writes .md.  The chat loop
  never writes .md directly.  No drift.
- Idempotent: re-projecting a session that's already on disk is a no-op
  (skip files that already exist).
- Best-effort: never raises.  A projection failure is logged but doesn't
  break the chat loop.

See .hermes/plans/2026-08-29_session-log-vault-projection.md for the full
design doc.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from paths import VAULT_ROOT

if TYPE_CHECKING:
    from services import Services

_log = logging.getLogger(__name__)

# Where the projected event .md files live in the vault.
LOGS_DIR_NAME = "Memory/Logs"

# Frontmatter marker so we can detect already-projected files.
_EVENT_MARKER = "<!-- vaultbot:log-event -->"

# Maximum body chars before an event gets a condensed block.  Long tool
# outputs (search results, web extracts) are common; we don't want to stuff
# 50K of raw JSON into a vault .md.  The condensed block is a simple
# head+tail truncation (no LLM) — enough for retrieval to route, and the
# raw is preserved for trace resolution.
_CONDENSE_THRESHOLD = 4000
_CONDENSE_HEAD = 800
_CONDENSE_TAIL = 800


def _logs_dir() -> Path:
    """The vault directory where session log .md files are projected."""
    d = VAULT_ROOT / "vaultbot-stuff" / LOGS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(text: str, max_len: int = 80) -> str:
    """Make a filesystem-safe filename from arbitrary text."""
    # Keep alnum, dashes, underscores; collapse everything else.
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-")
    if not s:
        s = "untitled"
    return s[:max_len]


def _frontmatter(
    provenance: list[str],
    session_id: str,
    event_num: int,
    timestamp: str | float | None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Build YAML frontmatter for an event .md file."""
    fm = [
        "---",
        "type: event",
        f"provenance: [{', '.join(provenance)}]",
        f"session: {session_id}",
        f"event: {event_num}",
    ]
    if timestamp is not None:
        if isinstance(timestamp, (int, float)):
            from datetime import UTC, datetime

            ts = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
        else:
            ts = str(timestamp)
        fm.append(f"timestamp: {ts}")
    if extra:
        for k, v in extra.items():
            fm.append(f"{k}: {v}")
    fm.append("---")
    fm.append("")
    return "\n".join(fm)


def _condense_body(body: str) -> str:
    """Truncate a long body into a condensed block + raw block.

    No LLM — just head+tail truncation.  Enough for retrieval to route;
    the raw is preserved for humans and trace resolution.
    """
    if len(body) <= _CONDENSE_THRESHOLD:
        return body
    head = body[:_CONDENSE_HEAD]
    tail = body[-_CONDENSE_TAIL:]
    skipped = len(body) - _CONDENSE_HEAD - _CONDENSE_TAIL
    return (
        f"## Condensed\n\n{head}\n\n"
        f"... (*{skipped} chars omitted*)\n\n"
        f"{tail}\n\n"
        f"## Raw\n\n<details>\n<summary>Full output ({len(body)} chars)</summary>\n\n"
        f"{body}\n\n</details>"
    )


def _write_event(
    session_dir: Path,
    event_num: int,
    provenance: list[str],
    session_id: str,
    body: str,
    timestamp: str | float | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Write one event .md file.  Returns the file path, or None on failure.

    Idempotent: if the file already exists, skip (don't overwrite).
    """
    filename = f"event-{event_num:04d}.md"
    path = session_dir / filename
    if path.exists():
        return str(path)

    try:
        content = _frontmatter(provenance, session_id, event_num, timestamp, extra)
        content += _EVENT_MARKER + "\n\n"
        content += _condense_body(body)
        content += "\n"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.debug("log_projection write_event failed: %s", e)
        return None


def _parse_jsonl(log_path: Path) -> list[dict[str, Any]]:
    """Parse a session JSONL file into a list of event dicts."""
    events: list[dict[str, Any]] = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.debug("log_projection parse_jsonl failed: %s", e)
    return events


def _build_events(jsonl_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group JSONL lines into projection events.

    Groups tool_call + tool_call_result by call_id.  Keeps user/assistant
    messages and thinking traces as standalone events.  Skips dev-only
    events (stage, route_decision, turn_cost, etc.) — those stay in the
    JSONL and never enter the vault.

    Returns a list of dicts with keys: provenance, body, timestamp, extra.
    """
    # Index tool_call_result by call_id for pairing.
    results_by_call_id: dict[int, dict[str, Any]] = {}
    for ev in jsonl_events:
        if ev.get("event") == "tool_call_result":
            data = ev.get("data", {})
            cid = data.get("call_id")
            if cid is not None:
                results_by_call_id[cid] = data

    projection_events: list[dict[str, Any]] = []

    # Track which call_ids we've already emitted so we don't duplicate.
    emitted_call_ids: set[int] = set()

    for ev in jsonl_events:
        event_type = ev.get("event", "")
        data = ev.get("data", {})
        ts = ev.get("timestamp")

        # User message (websocket_message direction=in with a user payload).
        if event_type == "websocket_message" and data.get("direction") == "in":
            payload = data.get("payload", {})
            msg = payload.get("message") or payload.get("content") or ""
            if msg and len(msg.strip()) > 0:
                projection_events.append(
                    {
                        "provenance": ["user"],
                        "body": msg,
                        "timestamp": ts,
                        "extra": None,
                    }
                )

        # Assistant message (websocket_message direction=out with content).
        elif event_type == "websocket_message" and data.get("direction") == "out":
            payload = data.get("payload", {})
            msg = payload.get("content") or payload.get("message") or ""
            if msg and len(msg.strip()) > 0:
                projection_events.append(
                    {
                        "provenance": ["assistant"],
                        "body": msg,
                        "timestamp": ts,
                        "extra": None,
                    }
                )

        # Tool call (paired with its result by call_id).
        elif event_type == "tool_call":
            call_id = data.get("call_id")
            if call_id is not None and call_id not in emitted_call_ids:
                emitted_call_ids.add(call_id)
                tool_name = data.get("tool", "unknown")
                method = data.get("method", "")
                inputs = data.get("inputs")
                result_data = results_by_call_id.get(call_id, {})
                output = result_data.get("result")
                error = result_data.get("error") or data.get("error")

                body_parts: list[str] = []
                if method:
                    body_parts.append(f"**Tool:** {tool_name}.{method}")
                else:
                    body_parts.append(f"**Tool:** {tool_name}")
                if inputs:
                    try:
                        _inputs_json = json.dumps(inputs, indent=2, default=str)[:2000]
                        body_parts.append(f"**Inputs:**\n```json\n{_inputs_json}\n```")
                    except Exception:  # noqa: BLE001 — best-effort serialization
                        body_parts.append(f"**Inputs:** {str(inputs)[:2000]}")
                if output is not None:
                    try:
                        out_str = (
                            json.dumps(output, indent=2, default=str)
                            if not isinstance(output, str)
                            else output
                        )
                    except Exception:  # noqa: BLE001 — best-effort serialization
                        out_str = str(output)
                    body_parts.append(f"**Output:**\n{out_str}")
                if error:
                    body_parts.append(f"**Error:** {error}")

                extra: dict[str, Any] = {"tool": tool_name}
                if method:
                    extra["method"] = method
                if call_id is not None:
                    extra["call_id"] = call_id

                projection_events.append(
                    {
                        "provenance": [f"tool:{tool_name}"],
                        "body": "\n\n".join(body_parts),
                        "timestamp": ts,
                        "extra": extra,
                    }
                )

        # Thinking trace — if the assistant message has a thinking block,
        # it's carried as a separate event.  The chat loop passes
        # thinking_text to the background task; we don't see it as a
        # separate JSONL event, so we skip it here.  Thinking is embedded
        # in the assistant websocket_message if present.
        # (Future: if thinking is logged as its own event, add it here.)

    return projection_events


def project_session(
    session_id: str,
    log_path: Path | None = None,
    svc: Services | None = None,
) -> int:
    """Project one session's JSONL log into vault .md event files.

    Reads the JSONL, groups events, writes one .md per event.  Idempotent:
    existing files are skipped.  Optionally wikilinks the event files to
    existing vault notes (when ``svc`` is provided).

    Returns the number of event files written (0 if nothing new).
    Never raises.
    """
    if not session_id:
        return 0

    # Find the JSONL log file.
    if log_path is None:
        log_path = Path(__file__).parent / "sessions" / f"{session_id}.jsonl"
    if not log_path.exists():
        _log.debug("log_projection: log not found %s", log_path)
        return 0

    # Parse + group.
    jsonl_events = _parse_jsonl(log_path)
    if not jsonl_events:
        return 0

    events = _build_events(jsonl_events)
    if not events:
        return 0

    # Write event files.
    session_dir = _logs_dir() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    written_paths: list[str] = []
    for i, ev in enumerate(events, start=1):
        path = _write_event(
            session_dir=session_dir,
            event_num=i,
            provenance=ev["provenance"],
            session_id=session_id,
            body=ev["body"],
            timestamp=ev["timestamp"],
            extra=ev.get("extra"),
        )
        if path:
            written += 1
            written_paths.append(path)

    # Wikilink the event files to existing vault notes.
    if written > 0 and svc is not None:
        _wikilink_events(svc, written_paths)

    # Write/update the session index.md (human-readable rollup).
    if written > 0:
        _write_session_index(session_dir, session_id, events)

    return written


def _wikilink_events(svc: Services, paths: list[str]) -> None:
    """Link event .md files to existing vault notes via weaving.link_outbound.

    Reuses the existing title-matching machinery — no new linking code.
    Best-effort: never raises.
    """
    try:
        from weaving import existing_note_titles, link_outbound

        title_map = existing_note_titles(svc)
        if not title_map:
            return
        for path in paths:
            try:
                link_outbound(path, title_map)
            except Exception as e:  # noqa: BLE001 — best-effort
                _log.debug("log_projection wikilink failed for %s: %s", path, e)
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.debug("log_projection wikilink_events failed: %s", e)


def _write_session_index(
    session_dir: Path,
    session_id: str,
    events: list[dict[str, Any]],
) -> None:
    """Write a human-readable index.md that links all events in order.

    This is the one place a human reads a whole session — it links to
    each event file in order with a short label.
    """
    index_path = session_dir / "index.md"
    try:
        lines = [
            f"# Session {session_id[:8]}",
            "",
            f"> session: {session_id}",
            f"> events: {len(events)}",
            "",
            "## Events",
            "",
        ]
        for i, ev in enumerate(events, start=1):
            prov = ", ".join(ev["provenance"])
            # First 60 chars of body as a label.
            label = ev["body"].replace("\n", " ").strip()[:60]
            lines.append(f"- [[event-{i:04d}]] `[{prov}]` — {label}")
        lines.append("")
        index_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.debug("log_projection write_session_index failed: %s", e)


def project_current_session(
    session_id: str,
    svc: Services | None = None,
) -> int:
    """Project the current session's JSONL log to the vault.

    Convenience wrapper for the chat loop: pass the session_id and
    optional services (for wikilinking).  Returns the count of new
    event files written.  Idempotent and never raises.
    """
    return project_session(session_id, svc=svc)
