"""Chat-loop checkpoint + resume — multi-day sturdiness for the agentic loop.

THE PROBLEM THIS SOLVES
-----------------------
The chat loop can run many tool rounds for a single user turn (research →
read → synthesize → verify). If the backend crashes or is restarted mid-turn,
all that work is lost: the next session starts the turn from scratch. The
existing ``checkpointer.py`` is research-cycle-specific (it snapshots the
autonomous researcher's gap list), not the per-round chat loop. This module
is the chat-loop equivalent: a durable snapshot of an in-flight turn so a
restart RESUMES mid-turn instead of restarting it.

This is the LangGraph-style durable-state piece, and the single biggest
"runs for days without losing the plot" gap in the chat path.

WHAT GETS SNAPSHOT
------------------
Per in-flight turn (one file, ``chat_loop_checkpoint.json``):
  - ``user_message``  — the turn being worked on
  - ``round_idx``     — which tool round we were on
  - ``accumulated``   — the final answer text streamed so far (if any)
  - ``thinking``      — thinking text so far
  - ``tool_history``  — compact list of {tool, args, result_summary} per round
                        so the resumed turn doesn't re-run tools it already ran
  - ``working_memory``— the TaskList snapshot (goal + task statuses)
  - ``updated_at``    — ISO timestamp (for staleness checks)

DESIGN
------
- ONE file, atomic write (temp + os.replace) with a lock — the chat loop and
  a recovery read never tear it. Same pattern as checkpointer.py.
- Best-effort: a checkpoint save/load failure must NEVER crash the chat loop.
- Bounded: tool_history stores *summaries* (via the same truncation the loop
  already applies), not full 50K tool results, so the file stays small.
- Cleared on normal turn completion and on ``/new``. A stale checkpoint older
  than ``MAX_AGE_S`` is ignored on load (a turn from yesterday isn't resumed).

Pure stdlib. No LLM calls.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from session_logger import SessionLoggerProtocol

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

# A checkpoint older than this is treated as stale and not resumed. A turn
# that started hours ago and never finished is almost certainly abandoned;
# resuming it would confuse a fresh session. Default 2 hours.
MAX_AGE_S = float(os.getenv("VAULTBOT_CHAT_CHECKPOINT_MAX_AGE_S", "7200"))


def _now_iso() -> str:
    return datetime.now().isoformat()


class ChatLoopCheckpointer:
    """Durable per-turn checkpoint for the agentic chat loop.

    When ``session_id`` is provided the checkpoint file is namespaced per
    session (``session_state/chat_loop_checkpoint_<session_id>.json``) so
    concurrent tabs don't stomp each other's in-flight turn state.  The
    legacy single-file path (``chat_loop_checkpoint.json``) is still used
    when no ``session_id`` is given (back-compat for tests).
    """

    # Directory for per-session checkpoint files.
    _SESSIONS_DIR = Path(__file__).with_name("session_state")

    def __init__(
        self,
        state_path: str | Path | None = None,
        session_logger: SessionLoggerProtocol | None = None,
        session_id: str | None = None,
    ):
        if state_path is None:
            if session_id:
                self._SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                state_path = (
                    self._SESSIONS_DIR / f"chat_loop_checkpoint_{session_id}.json"
                )
            else:
                state_path = Path(__file__).with_name("chat_loop_checkpoint.json")
        self.state_path = Path(state_path)
        self.session_logger = session_logger
        self.session_id = session_id

    @classmethod
    def for_session(
        cls, session_id: str, session_logger: SessionLoggerProtocol | None = None
    ) -> ChatLoopCheckpointer:
        """Create a per-session checkpointer."""
        return cls(
            state_path=None, session_logger=session_logger, session_id=session_id
        )

    # ------------------------------------------------------------------
    def _log(self, event: str, data: dict[str, Any]) -> None:
        try:
            if self.session_logger is not None:
                self.session_logger.log(event, data)
        except Exception as e:  # noqa: BLE001 — checkpoint logging is best-effort; print so the failure is visible, not silent
            print(f"[ChatLoopCheckpointer] _log failed: {e}")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        directory = str(path.parent) or "."
        Path(directory).mkdir(parents=True, exist_ok=True)
        with _write_lock:
            fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=path.name, dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                last: Exception | None = None
                for attempt in range(5):
                    try:
                        os.replace(tmp, str(path))
                        return
                    except OSError as e:  # WinError 32 sharing violation — retry
                        last = e
                        if attempt < 4:
                            time.sleep(0.05 * (attempt + 1))
                if last:
                    raise last
            except Exception:  # noqa: BLE001 — temp cleanup is best-effort; print so the failure is visible
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception as ce:  # noqa: BLE001 — temp cleanup is best-effort; print so the failure is visible
                    print(f"[ChatLoopCheckpointer] temp cleanup failed: {ce}")
                raise

    # ------------------------------------------------------------------
    def save(self, state: dict[str, Any]) -> None:
        """Persist the in-flight turn state. Never raises."""
        try:
            state = dict(state)
            state["updated_at"] = _now_iso()
            state["_ts"] = time.time()
            self._atomic_write(
                self.state_path, json.dumps(state, ensure_ascii=False, default=str)
            )
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log("chat_checkpoint_save_failed", {"error": str(e)})
            logger.warning("ChatLoopCheckpointer: save failed (non-fatal): %s", e)

    def load(self, max_age_s: float = MAX_AGE_S) -> dict[str, Any] | None:
        """Load the in-flight turn state, or None if absent/corrupt/stale."""
        try:
            if not self.state_path.exists():
                return None
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict) or not state.get("user_message"):
                return None
            ts = state.get("_ts")
            if isinstance(ts, (int, float)) and (time.time() - ts) > max_age_s:
                self._log("chat_checkpoint_stale", {"age_s": time.time() - ts})
                return None
            return state
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log("chat_checkpoint_load_failed", {"error": str(e)})
            return None

    def clear(self) -> None:
        """Remove the checkpoint (normal completion or /new). Never raises."""
        try:
            if self.state_path.exists():
                self.state_path.unlink()
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log("chat_checkpoint_clear_failed", {"error": str(e)})


def snapshot_working_memory(wm: Any) -> dict[str, Any]:
    """Best-effort snapshot of a TaskList for the checkpoint."""
    try:
        snap = wm.snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception as e:  # noqa: BLE001 — checkpoint snapshot is best-effort; print so the failure is visible, not silent
        print(f"[ChatLoopCheckpointer] snapshot_working_memory failed: {e}")
        return {}
