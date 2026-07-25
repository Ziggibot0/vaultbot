"""
checkpointer.py — VaultBot autonomous-researcher checkpoint layer.

If the backend crashes mid-research, this module lets it resume from where it
left off instead of losing the in-flight work. The design follows the
**OpenHands event-sourcing pattern** (arXiv:2511.03690): state is a pure
function of an append-only record of what has happened, and recovery = replay
the record to rebuild state, then continue. Concretely we persist a small
JSON snapshot of the current research cycle's gaps and their statuses; on
startup we look for any gap that was marked "running" when the process died
and hand those back to the caller (main.py) to re-queue.

The atomic-write helper mirrors the one in ``identity.py``: a module-level
``threading.Lock`` serializes the critical section and a short retry loop
rides out Windows WinError 32 (sharing-violation) when an external process
(Obsidian, antivirus, the indexer) briefly holds the target file open. This
is the same resilience LangGraph checkpointers rely on for durable state
under concurrent access.

Pure stdlib (dataclasses, json, pathlib, tempfile, threading, time, os,
typing). No new dependencies. Every method is wrapped in try/except, logs
errors via the optional ``session_logger``, and returns a sensible empty
result — a checkpoint failure must never take the researcher down.
"""

from __future__ import annotations

import json
import os
import time
import tempfile
import threading
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Serialize the atomic-write critical section. The autonomous researcher,
# the chat loop, and a recovery pass on startup can all touch the checkpoint
# file concurrently; on Windows os.replace() fails with WinError 32 if the
# target is briefly open in another process (Obsidian, antivirus, indexer).
# The lock serializes the write; the retry loop rides out the contention.
_write_lock = threading.Lock()


@dataclass
class ResearchCheckpoint:
    """A single piece of in-flight research work, persisted to disk.

    Attributes mirror the gap dict produced by ``AutonomousResearcher._identify_gaps``
    plus the lifecycle metadata needed to resume after a crash.
    """

    topic: str
    kind: str  # dangling_link / thin_note / orphan / etc.
    status: str  # pending / running / done / failed
    started_at: str
    completed_at: Optional[str] = None
    note_path: Optional[str] = None
    error: Optional[str] = None
    gap: Dict[str, Any] = field(default_factory=dict)


class Checkpointer:
    """Durable checkpoint layer for the autonomous research cycle.

    Stores two artifacts in ``checkpoint_dir``:
      * ``research_checkpoint.json`` — the list of ``ResearchCheckpoint`` for
        the current cycle (the work-in-flight snapshot).
      * ``cycle_state.json`` — the full cycle state (which gaps are
        done/running/pending, timestamp, round counter) for richer recovery.

    Both are written atomically. Reads are defensive: a missing or corrupt
    file yields an empty result and a logged warning, never an exception.
    """

    def __init__(
        self,
        checkpoint_dir: str = "vaultbot_backend/checkpoints",
        session_logger: Any = None,
    ) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.session_logger = session_logger
        self.checkpoint_path = self.checkpoint_dir / "research_checkpoint.json"
        self.cycle_state_path = self.checkpoint_dir / "cycle_state.json"
        try:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            self._safe_log(
                "checkpointer_init_failed", {"dir": str(self.checkpoint_dir), "error": str(e)}
            )
            logger.warning("Checkpointer: could not create checkpoint dir %s: %s",
                           self.checkpoint_dir, e)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _safe_log(self, event: str, data: Dict[str, Any]) -> None:
        """Log via session_logger if present, else no-op. Never raises."""
        try:
            if self.session_logger is not None:
                self.session_logger.log(event, data)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        """Write to a temp file then ``os.replace`` to avoid torn writes —
        the user may be editing nearby vault files in Obsidian at the same
        moment, and an antivirus/indexer may briefly hold our target open.

        Acquires the module-level write lock to serialize concurrent
        checkpoint saves (research loop + recovery + manual calls), and
        retries the ``os.replace`` a few times to ride out a transient lock
        held by an external process (Obsidian, antivirus, indexer).
        """
        directory = os.path.dirname(path) or "."
        max_retries = 5
        with _write_lock:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".tmp_", suffix=os.path.basename(path), dir=directory
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                last_err: Optional[Exception] = None
                for attempt in range(max_retries):
                    try:
                        os.replace(tmp_path, path)
                        return
                    except OSError as e:
                        last_err = e
                        # WinError 32 (sharing violation) is retryable; others
                        # (e.g. ENOENT) are not — but we still retry a few
                        # times before giving up so transient contention clears.
                        if attempt < max_retries - 1:
                            time.sleep(0.1 * (attempt + 1))
                if last_err:
                    raise last_err
            except Exception:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:  # noqa: BLE001
                    pass
                raise

    def _read_json(self, path: Path) -> Any:
        """Read+parse a JSON file. Returns None if missing/corrupt (logs)."""
        try:
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as e:  # noqa: BLE001
            self._safe_log("checkpointer_read_failed", {"path": str(path), "error": str(e)})
            logger.warning("Checkpointer: could not read %s: %s", path, e)
            return None

    # ------------------------------------------------------------------ #
    # Checkpoint list (the work-in-flight snapshot)
    # ------------------------------------------------------------------ #

    def save(self, checkpoints: List[ResearchCheckpoint]) -> None:
        """Serialize and atomically write the checkpoint list. Never crashes."""
        try:
            payload = json.dumps([asdict(c) for c in checkpoints], indent=2, ensure_ascii=False)
            self._atomic_write(str(self.checkpoint_path), payload)
        except Exception as e:  # noqa: BLE001
            self._safe_log(
                "checkpointer_save_failed",
                {"path": str(self.checkpoint_path), "error": str(e), "count": len(checkpoints)},
            )
            logger.warning("Checkpointer: save failed (non-fatal): %s", e)

    def load(self) -> List[ResearchCheckpoint]:
        """Read and deserialize the checkpoint list. Returns [] if missing/corrupt."""
        data = self._read_json(self.checkpoint_path)
        if not data or not isinstance(data, list):
            return []
        out: List[ResearchCheckpoint] = []
        for item in data:
            try:
                if not isinstance(item, dict):
                    continue
                out.append(
                    ResearchCheckpoint(
                        topic=str(item.get("topic", "")),
                        kind=str(item.get("kind", "unknown")),
                        status=str(item.get("status", "pending")),
                        started_at=str(item.get("started_at", "")),
                        completed_at=item.get("completed_at"),
                        note_path=item.get("note_path"),
                        error=item.get("error"),
                        gap=item.get("gap") if isinstance(item.get("gap"), dict) else {},
                    )
                )
            except Exception as e:  # noqa: BLE001
                self._safe_log("checkpointer_item_decode_failed", {"error": str(e)})
                logger.warning("Checkpointer: skipping corrupt checkpoint item: %s", e)
                continue
        return out

    def has_interrupted_work(self) -> bool:
        """True if any checkpoint has status='running' (in-flight when crash hit)."""
        try:
            return any(c.status == "running" for c in self.load())
        except Exception as e:  # noqa: BLE001
            self._safe_log("checkpointer_has_interrupted_failed", {"error": str(e)})
            return False

    def get_interrupted_work(self) -> List[ResearchCheckpoint]:
        """Return only the checkpoints with status='running' — the work to resume."""
        try:
            return [c for c in self.load() if c.status == "running"]
        except Exception as e:  # noqa: BLE001
            self._safe_log("checkpointer_get_interrupted_failed", {"error": str(e)})
            return []

    # ------------------------------------------------------------------ #
    # Full cycle state (richer recovery context)
    # ------------------------------------------------------------------ #

    def save_cycle_state(self, cycle_state: Dict[str, Any]) -> None:
        """Persist the full cycle state (gaps, statuses, timestamp). Atomic."""
        try:
            payload = json.dumps(cycle_state, indent=2, ensure_ascii=False)
            self._atomic_write(str(self.cycle_state_path), payload)
        except Exception as e:  # noqa: BLE001
            self._safe_log(
                "checkpointer_save_cycle_failed",
                {"path": str(self.cycle_state_path), "error": str(e)},
            )
            logger.warning("Checkpointer: save_cycle_state failed (non-fatal): %s", e)

    def load_cycle_state(self) -> Dict[str, Any]:
        """Load the full cycle state. Returns {} if missing/corrupt."""
        data = self._read_json(self.cycle_state_path)
        if not data or not isinstance(data, dict):
            return {}
        return data

    # ------------------------------------------------------------------ #
    # Lifecycle + recovery
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """Delete the checkpoint files (called after a clean cycle completion)."""
        for path in (self.checkpoint_path, self.cycle_state_path):
            try:
                if path.exists():
                    path.unlink()
            except Exception as e:  # noqa: BLE001
                self._safe_log("checkpointer_clear_failed", {"path": str(path), "error": str(e)})
                logger.warning("Checkpointer: could not remove %s: %s", path, e)

    def summary(self) -> Dict[str, Any]:
        """Status-endpoint summary: counts + whether interrupted work exists."""
        try:
            checkpoints = self.load()
            running = sum(1 for c in checkpoints if c.status == "running")
            done = sum(1 for c in checkpoints if c.status == "done")
            failed = sum(1 for c in checkpoints if c.status == "failed")
            return {
                "total": len(checkpoints),
                "running": running,
                "done": done,
                "failed": failed,
                "has_interrupted": running > 0,
            }
        except Exception as e:  # noqa: BLE001
            self._safe_log("checkpointer_summary_failed", {"error": str(e)})
            return {"total": 0, "running": 0, "done": 0, "failed": 0, "has_interrupted": False}

    def recover(self, researcher: Any) -> Dict[str, Any]:
        """Recovery entry point. Called on backend startup.

        If there is interrupted work (a checkpoint left in 'running' state
        when the previous process died), log a warning and return the
        interrupted checkpoints so the caller (main.py startup) can re-queue
        them with the researcher. The researcher object is accepted for
        interface stability / future direct re-queueing; this method does
        not mutate it — re-queueing is the caller's responsibility so the
        control flow stays in main.py.

        Returns ``{"recovered": [...], "skipped": bool}`` where ``recovered``
        is a list of ``ResearchCheckpoint`` and ``skipped`` is True when
        there was nothing to resume.
        """
        try:
            if not self.has_interrupted_work():
                self._safe_log("checkpointer_recover_skipped", {"reason": "no_interrupted_work"})
                return {"recovered": [], "skipped": True}
            interrupted = self.get_interrupted_work()
            self._safe_log(
                "checkpointer_recover_found",
                {"count": len(interrupted), "topics": [c.topic for c in interrupted]},
            )
            logger.warning(
                "Checkpointer: recovering %d interrupted research task(s): %s",
                len(interrupted),
                [c.topic for c in interrupted],
            )
            return {"recovered": interrupted, "skipped": False}
        except Exception as e:  # noqa: BLE001
            self._safe_log("checkpointer_recover_failed", {"error": str(e)})
            logger.warning("Checkpointer: recover failed (non-fatal): %s", e)
            return {"recovered": [], "skipped": True}