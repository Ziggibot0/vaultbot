"""
identity.py — VaultBot identity layer.

Manages one file in ``vaultbot_backend/identity/`` that makes VaultBot feel
like the *same agent* across days, regardless of which LLM is in the slot:

- ``IDENTITY.md`` — stable self-concept (human-seeded, rarely changes,
                    always loaded verbatim).

The vault *is* the mind; the model is swappable plumbing. IDENTITY.md is
boot-injected each session so the agent "wakes up coherent." Continuity
across restarts is handled by ``conversation_state.json`` and
``RESTART_CONTEXT.md`` — the actual conversation thread, not a stale summary.

Pure stdlib. No new dependencies.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Any

from config import TUNABLES

logger = logging.getLogger(__name__)

# Serialize writes to the identity file. On Windows os.replace() can fail
# with WinError 32 if the target file is open by another process (Obsidian,
# antivirus). The lock serializes the atomic-write critical section; the
# retry covers the case where an external process briefly holds the file open.
_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Rough chars/token estimate for the status endpoint (consolidated in config).
_CHARS_PER_TOKEN = TUNABLES.chars_per_token

# Filenames inside identity_dir.
_IDENTITY_FILENAME = "IDENTITY.md"
_RESTART_CONTEXT_FILENAME = "RESTART_CONTEXT.md"

# ---------------------------------------------------------------------------
# Seed content
# ---------------------------------------------------------------------------

_SEED_IDENTITY = (
    "I am VaultBot, a self-improving research agent in an Obsidian vault. "
    "My mind is the vault's interconnected notes and skills, not my model "
    "weights — the model is swappable plumbing. My job is to research gaps, "
    "write linked notes, learn new skills, and help my operator think."
)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class Identity:
    """Identity layer for VaultBot.

    The identity dir is created on init and seeded with defaults if absent.
    Every method is wrapped so that a failure can never crash the chat loop —
    errors are logged and sensible defaults are returned.
    """

    def __init__(
        self,
        identity_dir: str = "vaultbot_stuff/vaultbot_backend/identity",
        ollama_client: Any = None,
        session_logger: Any = None,
    ) -> None:
        self.identity_dir = identity_dir
        self.ollama_client = ollama_client
        self.session_logger = session_logger

        self._identity_path = os.path.join(identity_dir, _IDENTITY_FILENAME)
        self._restart_context_path = os.path.join(identity_dir, _RESTART_CONTEXT_FILENAME)

        try:
            os.makedirs(identity_dir, exist_ok=True)
            self._seed_if_missing()
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.exception("Identity init failed: %s", exc)
            self._safe_log("identity_init_error", {"error": str(exc)})

        # ---- boot_context cache ------------------------------------------
        # boot_context() reads IDENTITY.md on every chat turn to inject it
        # verbatim into the system prompt. It changes rarely, so cache the
        # assembled string keyed on the file's mtime. A stat() per turn is
        # far cheaper than a read_text() call.
        self._boot_cache: str | None = None
        self._boot_cache_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _seed_if_missing(self) -> None:
        """Seed IDENTITY.md with the default if it does not yet exist."""
        if not os.path.exists(self._identity_path):
            self._atomic_write(self._identity_path, _SEED_IDENTITY)
            self._safe_log("identity_seed", {"file": self._identity_path})

    # ------------------------------------------------------------------
    # Boot injection
    # ------------------------------------------------------------------

    def boot_context(self) -> str:
        """Read IDENTITY.md and return it for injection into the system
        prompt, verbatim. Order: RESTART_CONTEXT (if present) + IDENTITY.

        This is the "boot injection" — delivered before the first turn, never
        summarized. Cached on the file's mtime so consecutive turns in a
        session skip the disk read; the cache is invalidated automatically
        whenever IDENTITY.md is edited.

        If RESTART_CONTEXT.md exists (written by the backend_restart tool
        before triggering a restart), it is prepended to the boot context and
        then deleted — one-shot injection so the agent wakes up after a
        restart already knowing what was happening. The restart context is
        NOT included in the cache, so it only appears on the first boot
        after restart.
        """
        try:
            # Check for one-shot restart context first. This is consumed
            # (read + deleted) immediately, then prepended to the return
            # value. It is NOT stored in the boot cache, so subsequent
            # boot_context() calls in the same session won't include it.
            restart_ctx = self._consume_restart_context()

            # Cache check: stat IDENTITY.md and compare to the cached mtime.
            current_mtime = 0.0
            try:
                current_mtime = os.path.getmtime(self._identity_path)
            except OSError:
                current_mtime = float("inf")

            if (
                self._boot_cache is not None
                and current_mtime == self._boot_cache_mtime
            ):
                self._safe_log("identity_boot_cache_hit", {})
                if restart_ctx:
                    return restart_ctx + "\n\n" + self._boot_cache
                return self._boot_cache

            identity = self.get_identity()
            assembled = "# IDENTITY\n" + identity if identity else ""
            self._boot_cache = assembled
            if current_mtime != float("inf"):
                self._boot_cache_mtime = current_mtime
            if restart_ctx:
                return restart_ctx + "\n\n" + assembled
            return assembled
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.exception("boot_context failed: %s", exc)
            self._safe_log("identity_boot_error", {"error": str(exc)})
            return ""

    def _consume_restart_context(self) -> str:
        """Read and delete RESTART_CONTEXT.md if it exists (one-shot).

        Written by the backend_restart tool before triggering a restart.
        Contains recent chat history so the agent wakes up knowing what
        was happening. Consumed on the first boot_context() call after
        restart, then deleted so it doesn't persist across turns.
        """
        try:
            if os.path.exists(self._restart_context_path):
                content = self._read(self._restart_context_path)
                os.remove(self._restart_context_path)
                self._safe_log(
                    "identity_restart_context_consumed",
                    {"chars": len(content)},
                )
                return content
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.warning("Failed to consume restart context: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Identity mutators / readers
    # ------------------------------------------------------------------

    def set_identity(self, text: str) -> None:
        """Full-replace IDENTITY.md (rare; human or agent can call)."""
        try:
            self._atomic_write(self._identity_path, text.strip() + "\n")
            self._safe_log("identity_set", {"chars": len(text)})
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.exception("set_identity failed: %s", exc)
            self._safe_log("identity_set_error", {"error": str(exc)})

    def get_identity(self) -> str:
        return self._read(self._identity_path)

    # ------------------------------------------------------------------
    # Status summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """For the status endpoint."""
        try:
            identity = self.get_identity()
            return {
                "identity_chars": len(identity),
            }
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.exception("summary failed: %s", exc)
            self._safe_log("identity_summary_error", {"error": str(exc)})
            return {
                "identity_chars": 0,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read(self, path: str) -> str:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.warning("read failed for %s: %s", path, exc)
            return ""

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        """Write to a temp file then ``os.replace`` to avoid torn writes —
        the user may be editing the file in Obsidian at the same moment.

        Acquires the module-level write lock to serialize concurrent writes,
        and retries the ``os.replace`` a few times to ride out a transient
        lock held by an external process (Obsidian, antivirus, indexer).
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
                # On Windows the os.replace can fail with WinError 32 if
                # another process briefly has the target open. Retry with a
                # short backoff so we ride out the contention.
                last_err: Exception | None = None
                for attempt in range(max_retries):
                    try:
                        os.replace(tmp_path, path)
                        return
                    except OSError as e:
                        last_err = e
                        # WinError 32 (sharing violation) is retryable; others
                        # (e.g. ENOENT) are not.
                        if attempt < max_retries - 1:
                            time.sleep(0.1 * (attempt + 1))
                # Exhausted retries — raise so the caller can log + continue.
                if last_err:
                    raise last_err
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                # Clean up the temp file on failure if it still exists.
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    pass
                raise

    def _safe_log(self, event: str, data: dict[str, Any]) -> None:
        """Log via session_logger if present, else no-op. Never raises."""
        try:
            if self.session_logger is not None:
                self.session_logger.log(event, data)
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.debug("session_logger.log failed: %s", exc)
