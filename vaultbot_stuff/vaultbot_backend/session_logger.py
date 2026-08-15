"""Session logger — append-only JSONL audit trail with automatic secret redaction.

Every tool call, LLM interaction, WebSocket message, and internal event is
logged to a per-session .jsonl file. Secrets (API keys, tokens) are redacted
before writing. The log is the single source of truth for debugging and
calibration.
"""

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
_PROVIDER_KEY_RE = re.compile(
    r"(?:^(?:sk-|sk-or-|tvly-|xai-|sk-ant-)[A-Za-z0-9_\-]{8,}$"
    r"|^[A-Za-z0-9_\-]{24,}$)"
)


def _redact(obj: Any) -> Any:
    """Recursively replace secret-shaped values with ``[REDACTED]``.

    Walks dicts and lists; replaces string values that look like provider
    keys, and any value of a dict key whose name ends with a secret suffix.
    Cheap: only inspects strings. Non-string values pass through.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                # The KEY says it's a secret — redact regardless of value.
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    if isinstance(obj, str):
        if _PROVIDER_KEY_RE.search(obj):
            # Exclude UUIDs — they're identifiers (session_id, file stems),
            # not secrets. A bare 36-char hex+dash string is a UUID.
            if _UUID_RE.match(obj):
                return obj
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

    def __init__(self, log_dir: str | None = None):
        if log_dir is None:
            log_dir = Path(__file__).parent / "sessions"
        else:
            log_dir = Path(log_dir)
        self.log_dir = Path(log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Retention: cap session log accumulation so a non-technical user's
        # disk doesn't fill over months. Defaults live in config.TUNABLES.
        try:
            sweep_old_sessions(
                self.log_dir,
                max_files=TUNABLES.session_log_retention_count,
                max_age_days=TUNABLES.session_log_retention_days,
                max_file_mb=TUNABLES.session_log_max_file_mb,
            )
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass  # cleanup must never crash the backend

        self.session_id: str = str(uuid.uuid4())
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

    def log_tool_call(
        self,
        tool: str,
        method: str,
        inputs: dict[str, Any] | None = None,
        outputs: Any | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Log a tool/framework call with input, output, timing, and error."""
        self.log(
            "tool_call",
            {
                "tool": tool,
                "method": method,
                "inputs": inputs,
                "outputs": outputs,
                "duration_ms": duration_ms,
                "error": error,
            },
        )

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
