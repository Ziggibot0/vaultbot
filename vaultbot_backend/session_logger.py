import json
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
        # disk doesn't fill over months. Keep newest 200 files, delete
        # anything older than 30 days. Runs once per new session (cheap).
        try:
            sweep_old_sessions(self.log_dir)
        except Exception:
            pass  # cleanup must never crash the backend

        self.session_id: str = str(uuid.uuid4())
        self.started_at: str = datetime.now(UTC).isoformat()
        self._file_path = self.log_dir / f"{self.session_id}.jsonl"
        self._closed = False

        self._write({
            "event": "session_start",
            "session_id": self.session_id,
            "timestamp": self._now(),
            "started_at": self.started_at,
        })

    def _now(self) -> float:
        return time.time()

    def _write(self, record: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except Exception as e:
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
        self.log("tool_call", {
            "tool": tool,
            "method": method,
            "inputs": inputs,
            "outputs": outputs,
            "duration_ms": duration_ms,
            "error": error,
        })

    def log_message(self, direction: str, payload: dict[str, Any]) -> None:
        """Log a WebSocket message sent or received."""
        self.log("websocket_message", {
            "direction": direction,  # "in" or "out"
            "payload": payload,
        })

    def log_exception(self, exc: Exception | None = None, context: str | None = None) -> None:
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
        self.log("session_end", {
            "closed_at": datetime.now(UTC).isoformat(),
        })
        self._closed = True


def sweep_old_sessions(log_dir, max_files=200, max_age_days=30):
    """Delete old session JSONL files to cap disk usage.

    Keeps the newest `max_files` files and deletes any file older than
    `max_age_days` by mtime. Runs once per new SessionLogger (i.e. once
    per websocket connection). Never raises — cleanup is best-effort.

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
        for i, f in enumerate(files):
            is_too_old = f.stat().st_mtime < cutoff
            is_over_count = i >= max_files
            if is_too_old or is_over_count:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:
                    pass
        return deleted
    except Exception:
        return 0


# Singleton-style default logger used when no session is active.
_default_logger: SessionLogger | None = None


def get_default_logger(log_dir: str | None = None) -> SessionLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = SessionLogger(log_dir=log_dir)
    return _default_logger
