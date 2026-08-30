"""Session logger — append-only JSONL audit trail with automatic secret redaction.

Every tool call, LLM interaction, WebSocket message, and internal event is
logged to a per-session .jsonl file. Secrets (API keys, tokens) are redacted
before writing. The log is the single source of truth for debugging and
calibration.
"""

import contextlib
import json
import re
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import TUNABLES

# ── Secret redaction ──────────────────────────────────────────────────────
# Session logs persist full WebSocket payloads (user messages, tool inputs,
# error strings) to JSONL on disk. Any value that looks like an API key or
# token is replaced with [REDACTED] before serialization so a log file that
# leaks off the host (backup, sync, support share) doesn't carry credentials.
#
# Two signals are checked (cheaply, only on strings):
#   1. Value of a dict KEY whose name ends with a secret suffix
#      (api_key, key, secret, token, password, passphrase, credential).
#   2. String VALUE that matches a known provider key shape
#      (sk-..., tvly-..., and other long high-entropy alphanumeric tokens).
# Conservative: over-redacts rather than under-redacts. A false positive only
# replaces a log field with [REDACTED], never breaks the app.

_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|passphrase|credential)$",
    re.IGNORECASE,
)
# Provider key shapes: "sk-" (OpenAI), "tvly-" (Tavily), "sk-or-" (OpenRouter),
# "xai-" (xAI), plus generic long alnum-with-dashes tokens (>=24 chars, high
# entropy: letters+digits, optional dashes/underscores, no spaces).
# UUIDs (8-4-4-4-12 hex, 36 chars) are explicitly EXCLUDED so session_id and
# other UUID fields are NOT redacted — they're identifiers, not secrets.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# Provider key shapes: only match strings with a KNOWN provider key
# prefix. The previous bare ``^[A-Za-z0-9_\-]{24,}$`` alternative
# over-redacted legitimate diagnostic strings (search queries, note
# titles, error messages) that happened to be ≥24 chars of alnum.
# See issue #86 — the redaction must be conservative (never under-
# redact) but not so broad that it destroys diagnostic value.
#
# If a new provider is added, append its prefix here.
_PROVIDER_KEY_RE = re.compile(r"^(?:sk-|sk-or-|tvly-|xai-|sk-ant-)[A-Za-z0-9_\-]{8,}$")

# Field paths that are NEVER redacted regardless of their string value.
# These are known-safe by construction: user messages, search queries,
# note titles, tool names, model names. The path is matched against the
# dotted key path (e.g. ``data.payload.message``, ``data.query``).
# A key at any depth whose final segment matches one of these is
# exempt from _PROVIDER_KEY_RE value redaction. (Key-suffix redaction
# via _SECRET_KEY_RE still applies — ``data.api_key`` is always
# redacted even if ``api_key`` weren't in this allowlist, which it
# isn't.)
_SAFE_FIELD_NAMES = frozenset(
    {
        "message",  # websocket user message (data.payload.message)
        "content",  # assistant response (data.payload.content)
        "query",  # search queries (data.query)
        "topic",  # research topics (data.topic)
        "tool",  # tool names (data.tool)
        "method",  # tool method names (data.method)
        "model",  # model names (data.model)
        "title",  # session/note titles (data.title)
        "detail",  # stage detail strings (data.detail)
        "stage",  # stage names (data.stage)
        "context",  # exception context (data.context)
        "error",  # error message strings (data.error)
        "user_message",  # chat_begin user message (data.user_message)
        "source",  # provenance source labels (data.source)
        "name",  # tool call names (data.name)
        "msg",  # qa_worker messages (data.msg)
    }
)


def _redact(obj: Any, _key_path: str = "") -> Any:
    """Recursively replace secret-shaped values with ``[REDACTED]``.

    Walks dicts and lists; replaces string values that look like provider
    keys, and any value of a dict key whose name ends with a secret suffix.
    Cheap: only inspects strings. Non-string values pass through.

    ``_key_path`` is the dotted path of the current key (e.g.
    ``data.payload.message``) used to check the safe-field allowlist.
    Callers should not pass it — it's used internally during recursion.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            child_path = f"{_key_path}.{k}" if _key_path else str(k)
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                # The KEY says it's a secret — redact regardless of value.
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact(v, child_path)
        return out
    if isinstance(obj, list):
        return [_redact(item, _key_path) for item in obj]
    if isinstance(obj, str):
        # Allowlist: if the leaf key name is known-safe, skip value
        # redaction entirely. This prevents over-redaction of
        # legitimate diagnostic strings (e.g. a 30-char search query
        # stored under ``data.query``).
        leaf = _key_path.rsplit(".", 1)[-1] if _key_path else ""
        if leaf in _SAFE_FIELD_NAMES:
            return obj
        if _PROVIDER_KEY_RE.search(obj):
            return "[REDACTED]"
        return obj
    return obj


class SessionLogger:
    """
    Append-only JSONL session/event logger for VaultBot.

    One session maps to one WebSocket connection. All events emitted by any
    component during that session are written to a single line-delimited JSON
    file, making it trivial to replay or grep a request's full lifecycle.
    """

    def __init__(self, log_dir: str | None = None, session_id: str | None = None):
        """Create a new session logger.

        ``session_id`` lets the caller REUSE an existing session across a
        WebSocket reconnect (the frontend sends its last-known session_id
        back so a dropped-and-reconnected socket resumes the same session
        instead of minting a fresh UUID with zero history). When ``None``
        (legacy behavior) a new UUID is generated.
        """
        if log_dir is None:
            log_dir = Path(__file__).parent / "sessions"
        else:
            log_dir = Path(log_dir)
        self.log_dir = Path(log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Retention: cap session log accumulation so a non-technical user's
        # disk doesn't fill over months. Defaults live in config.TUNABLES.
        with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort cleanup
            sweep_old_sessions(
                self.log_dir,
                max_files=TUNABLES.session_log_retention_count,
                max_age_days=TUNABLES.session_log_retention_days,
                max_file_mb=TUNABLES.session_log_max_file_mb,
            )

        self.session_id: str = session_id or str(uuid.uuid4())
        self.started_at: str = datetime.now(UTC).isoformat()
        self._file_path = self.log_dir / f"{self.session_id}.jsonl"
        self._closed = False
        self.title: str = "New Session"
        # Session-level token cost accumulator. Updated per-turn via
        # add_token_usage() so the session log records the total cloud tokens
        # consumed across all turns. Used for cost analysis and tuning.
        self.token_totals: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        # Monotonic counter for tool-call correlation IDs. Every
        # log_tool_call() gets a unique ``call_id`` so the reader can
        # match a ``tool_call`` event to its ``tool_call_result`` event
        # deterministically — no more reversed-walk heuristic.
        # See issue #86, Fix #5.
        self._call_id_counter: int = 0

        self._write(
            {
                "event": "session_start",
                "session_id": self.session_id,
                "timestamp": self._now(),
                "started_at": self.started_at,
                "title": self.title,
            }
        )

    def set_title(self, title: str) -> None:
        """Set the session title and persist it to the log.

        Called when the user edits the title inline in the sidebar, or
        auto-generated from the first user message. The title is written
        as a ``session_title`` event so the /sessions listing can read it
        back without parsing the full conversation.
        """
        self.title = title[:200] if title else "New Session"
        self._write(
            {
                "event": "session_title",
                "session_id": self.session_id,
                "timestamp": self._now(),
                "title": self.title,
            }
        )

    def _now(self) -> float:
        return time.time()

    def _write(self, record: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            # Redact secrets before serialization so credentials never
            # land in the JSONL log file on disk.
            safe = _redact(record)
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(safe, default=str, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # Logging must never crash the application. Fail silently to stderr.
            print(f"[SessionLogger] Failed to write event: {e}")

    def log(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Log a generic event with optional payload."""
        record = {
            "event": event,
            "session_id": self.session_id,
            "timestamp": self._now(),
        }
        if data is not None:
            record["data"] = data
        self._write(record)

    def add_token_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Accumulate per-turn token counts into the session-level total.

        Called by the chat handler after each turn so the session log
        captures the cumulative cloud token cost. Never raises.
        """
        try:
            self.token_totals["prompt_tokens"] += prompt_tokens or 0
            self.token_totals["completion_tokens"] += completion_tokens or 0
        except Exception as e:  # noqa: BLE001 — token tracking is observability; a failure must not crash the chat loop, but it MUST be visible
            print(f"[SessionLogger] add_token_usage failed: {e}")

    def log_llm_invocation(
        self,
        *,
        role: str,
        model_id: str,
        provider_id: str,
        provider_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        token_source: str,
        stream: bool,
        duration_ms: float,
        outcome: str = "success",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Emit one immutable record for a completed LLM API invocation."""
        invocation_id = str(uuid.uuid4())
        data: dict[str, Any] = {
            "invocation_id": invocation_id,
            "role": role,
            "model_id": model_id,
            "provider_id": provider_id,
            "provider_type": provider_type,
            "model": model,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int((prompt_tokens or 0) + (completion_tokens or 0)),
            "token_source": token_source,
            "stream": bool(stream),
            "duration_ms": round(float(duration_ms or 0.0), 2),
            "outcome": outcome,
        }
        if context:
            data["context"] = dict(context)
        self.log("llm_invocation", data)
        return invocation_id

    # ── Per-turn orchestration attribution ───────────────────────────────

    def log_route_decision(
        self,
        route: str,
        confidence: float,
        reason: str,
        *,
        turn_index: int | None = None,
    ) -> None:
        """Emit a ``route_decision`` event for one turn.

        ``route`` must be one of ``deterministic``, ``small_model``,
        ``procedure``, or ``big_model``.  ``confidence`` is a float in
        [0, 1].  ``reason`` is a short reason code (e.g.
        ``action_signal``, ``clarification_or_explanation``,
        ``mixed_or_unsettled``).

        Never raises — routing attribution is observability-only and must
        not crash the chat loop.
        """
        try:
            data: dict[str, Any] = {
                "route": route,
                "confidence": round(float(confidence), 4),
                "reason": reason,
            }
            if turn_index is not None:
                data["turn_index"] = turn_index
            self.log("route_decision", data)
        except Exception as e:  # noqa: BLE001 — observability; must not crash the loop
            print(f"[SessionLogger] log_route_decision failed: {e}")

    def log_turn_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        tool_latency_ms: float = 0.0,
        cost_usd: float | None = None,
        model: str | None = None,
        turn_index: int | None = None,
    ) -> None:
        """Emit a ``turn_cost`` event with token counts and estimated cost.

        ``tool_latency_ms`` is the sum of all tool-call durations for
        this turn.  ``cost_usd`` is an optional pre-computed estimate
        (e.g. from a configurable per-token rate); when ``None`` it is
        omitted from the event so callers are not forced to implement
        billing logic.

        Never raises.
        """
        try:
            data: dict[str, Any] = {
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "total_tokens": int((prompt_tokens or 0) + (completion_tokens or 0)),
                "tool_latency_ms": round(float(tool_latency_ms or 0.0), 2),
            }
            if cost_usd is not None:
                data["cost_usd"] = round(float(cost_usd), 8)
            if model is not None:
                data["model"] = model
            if turn_index is not None:
                data["turn_index"] = turn_index
            self.log("turn_cost", data)
        except Exception as e:  # noqa: BLE001 — observability; must not crash the loop
            print(f"[SessionLogger] log_turn_cost failed: {e}")

    def log_turn_efficiency(
        self,
        tool_rounds: int,
        completion_outcome: str,
        *,
        repeated_tool_calls: list[str] | None = None,
        turn_index: int | None = None,
    ) -> None:
        """Emit a ``turn_efficiency`` event.

        ``tool_rounds`` is the number of LLM→tool round-trips in this
        turn.  ``completion_outcome`` is a short outcome label such as
        ``success``, ``fallback``, ``error``, or ``truncated``.
        ``repeated_tool_calls`` is an optional list of tool names that
        were called more than once (signals routing inefficiency).

        Never raises.
        """
        try:
            data: dict[str, Any] = {
                "tool_rounds": int(tool_rounds or 0),
                "completion_outcome": completion_outcome,
                "repeated_tool_calls": list(repeated_tool_calls or []),
            }
            if turn_index is not None:
                data["turn_index"] = turn_index
            self.log("turn_efficiency", data)
        except Exception as e:  # noqa: BLE001 — observability; must not crash the loop
            print(f"[SessionLogger] log_turn_efficiency failed: {e}")

    def log_tool_call(
        self,
        tool: str,
        method: str,
        inputs: dict[str, Any] | None = None,
        outputs: Any | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Log a tool/framework call with input, output, timing, and error.

        Emits a ``tool_call`` event with a unique ``call_id`` so the
        result can be correlated deterministically. The ``call_id`` is
        a monotonically incrementing integer scoped to this session.
        """
        self._call_id_counter += 1
        call_id = self._call_id_counter
        self.log(
            "tool_call",
            {
                "call_id": call_id,
                "tool": tool,
                "method": method,
                "inputs": inputs,
                "outputs": outputs,
                "duration_ms": duration_ms,
                "error": error,
            },
        )

    def log_tool_result(
        self,
        call_id: int,
        tool: str,
        result: Any | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Log a tool result correlated to a prior ``tool_call`` by ``call_id``.

        This is the companion to ``log_tool_call()``: the ``call_id``
        matches the one returned by that method, so the reader can
        pair them without the reversed-walk heuristic used by the old
        ``Analyze-Session-Log`` procedure.
        """
        self.log(
            "tool_call_result",
            {
                "call_id": call_id,
                "tool": tool,
                "result": result,
                "duration_ms": duration_ms,
                "error": error,
            },
        )

    def next_call_id(self) -> int:
        """Allocate and return the next tool-call correlation ID.

        Call sites that emit ``tool_call_requested`` (websocket-facing
        tool dispatch in ``chat_loop_tools.py``) can use this to get a
        ``call_id`` *before* the tool runs, then emit it in both the
        request and result events.
        """
        self._call_id_counter += 1
        return self._call_id_counter

    def log_message(self, direction: str, payload: dict[str, Any]) -> None:
        """Log a WebSocket message sent or received."""
        self.log(
            "websocket_message",
            {
                "direction": direction,  # "in" or "out"
                "payload": payload,
            },
        )

    def log_stage(self, stage: str, detail: str = "", **extra: Any) -> None:
        """Log a structured stage boundary with a stable schema.

        Emits a ``stage`` event: ``{stage, detail, round, tool, duration_ms,
        error, ...extra}``. Every phase boundary in chat_handler calls this
        so the session log has one scannable event type for "what stage are
        we in" without grepping 20 ad-hoc event names.

        Keyword args are merged into the event data (round, tool,
        duration_ms, error, etc.).
        """
        data: dict[str, Any] = {"stage": stage}
        if detail:
            data["detail"] = detail
        data.update(extra)
        self.log("stage", data)

    def log_exception(
        self, exc: Exception | None = None, context: str | None = None
    ) -> None:
        """Log an exception with traceback."""
        data: dict[str, Any] = {
            "traceback": traceback.format_exc() if exc or context else "",
        }
        if exc is not None:
            data["error"] = f"{type(exc).__name__}: {exc}"
        if context is not None:
            data["context"] = context
        self.log("exception", data)

    def close(self) -> None:
        """Finalize the session log file."""
        if self._closed:
            return
        # Log cumulative session-level token totals before closing.
        self.log(
            "session_token_total",
            {
                "prompt_tokens": self.token_totals["prompt_tokens"],
                "completion_tokens": self.token_totals["completion_tokens"],
                "total_tokens": (
                    self.token_totals["prompt_tokens"]
                    + self.token_totals["completion_tokens"]
                ),
            },
        )
        self.log(
            "session_end",
            {
                "closed_at": datetime.now(UTC).isoformat(),
            },
        )
        self._closed = True


def sweep_old_sessions(
    log_dir,
    max_files: int = TUNABLES.session_log_retention_count,
    max_age_days: int = TUNABLES.session_log_retention_days,
    max_file_mb: int = TUNABLES.session_log_max_file_mb,
):
    """Delete old session JSONL files to cap disk usage.

    Keeps the newest `max_files` files and deletes any file older than
    `max_age_days` by mtime. Also truncates any single file larger than
    `max_file_mb` to its last ~1000 lines (keeps the tail = most recent
    events). Runs once per new SessionLogger (i.e. once per websocket
    connection). Never raises — cleanup is best-effort.

    Returns the count of deleted files.
    """
    try:
        log_dir = Path(log_dir)
        if not log_dir.exists():
            return 0
        files = sorted(
            (f for f in log_dir.glob("*.jsonl") if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,  # newest first
        )
        deleted = 0
        cutoff = time.time() - max_age_days * 86400
        max_bytes = max_file_mb * 1024 * 1024
        for i, f in enumerate(files):
            is_too_old = f.stat().st_mtime < cutoff
            is_over_count = i >= max_files
            if is_too_old or is_over_count:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    pass
            elif f.stat().st_size > max_bytes:
                # Truncate oversized active logs: keep the last ~1000 lines
                # (most recent events) so debugging still has context.
                try:
                    lines = f.read_bytes().splitlines()
                    if len(lines) > 1000:
                        with open(f, "wb") as fh:
                            fh.write(b"\n".join(lines[-1000:]) + b"\n")
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    pass
        return deleted
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return 0


# Singleton-style default logger used when no session is active.
_default_logger: SessionLogger | None = None


def get_default_logger(log_dir: str | None = None) -> SessionLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = SessionLogger(log_dir=log_dir)
    return _default_logger
