import json
import os
import time
import uuid
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class SessionLogger:
    """
    Append-only JSONL session/event logger for VaultBot.

    One session maps to one WebSocket connection. All events emitted by any
    component during that session are written to a single line-delimited JSON
    file, making it trivial to replay or grep a request's full lifecycle.
    """

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            log_dir = Path(__file__).parent / "sessions"
        else:
            log_dir = Path(log_dir)
        self.log_dir = Path(log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.session_id: str = str(uuid.uuid4())
        self.started_at: str = datetime.now(timezone.utc).isoformat()
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

    def _write(self, record: Dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except Exception as e:
            # Logging must never crash the application. Fail silently to stderr.
            print(f"[SessionLogger] Failed to write event: {e}")

    def log(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
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
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Any] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
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

    def log_message(self, direction: str, payload: Dict[str, Any]) -> None:
        """Log a WebSocket message sent or received."""
        self.log("websocket_message", {
            "direction": direction,  # "in" or "out"
            "payload": payload,
        })

    def log_exception(self, exc: Optional[Exception] = None, context: Optional[str] = None) -> None:
        """Log an exception with traceback."""
        data: Dict[str, Any] = {
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
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })
        self._closed = True


# Singleton-style default logger used when no session is active.
_default_logger: Optional[SessionLogger] = None


def get_default_logger(log_dir: Optional[str] = None) -> SessionLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = SessionLogger(log_dir=log_dir)
    return _default_logger
