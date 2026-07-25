"""
identity.py — VaultBot three-file identity layer.

Manages three files in ``vaultbot_backend/identity/`` that make VaultBot feel
like the *same agent* across days, regardless of which LLM is in the slot:

- ``IDENTITY.md``  — stable self-concept (human-seeded, rarely changes,
                      always loaded verbatim).
- ``SELF_MODEL.md`` — MIRROR-style reconstructive narrative, agent-regenerated
                      each turn, ≤3000 tokens, always loaded as "previous state".
- ``GOALS.md``     — active goal/plan (agent-editable, bounded, always loaded).

Design lineage:
    * MIRROR  — bounded reconstructive state regenerated each turn yields
                +5-20% across 7 models (the "Cognitive Controller" pattern).
    * Letta   — pinned persona blocks injected on every boot.
    * Generative Agents — plan persistence (the GOALS.md file).

The vault *is* the mind; the model is swappable plumbing. These three files are
boot-injected each session so the agent "wakes up coherent."

Pure stdlib + optional ``ollama_client``. No new dependencies.
"""

from __future__ import annotations

import os
import time
import tempfile
import threading
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Serialize writes to the identity files. The chat loop and the autonomous
# researcher both call regenerate_self_model(), and on Windows os.replace()
# fails with WinError 32 if the target file is open by another process
# (Obsidian, antivirus, or a concurrent regeneration). The lock serializes
# the atomic-write critical section; the retry covers the case where an
# external process (Obsidian) briefly holds the file open.
_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ≈3000 tokens at the usual 4-chars/token heuristic. The LLM is asked to stay
# under 3000 tokens; this is a hard ceiling enforced *after* regeneration so a
# runaway model can never balloon the self-model across turns.
SELF_MODEL_MAX_CHARS = 12000

# Rough chars/token estimate for the status endpoint.
_CHARS_PER_TOKEN = 4

# Filenames inside identity_dir.
_IDENTITY_FILENAME = "IDENTITY.md"
_SELF_MODEL_FILENAME = "SELF_MODEL.md"
_GOALS_FILENAME = "GOALS.md"

# ---------------------------------------------------------------------------
# Seed content
# ---------------------------------------------------------------------------

_SEED_IDENTITY = (
    "I am VaultBot, a self-improving research agent in an Obsidian vault. "
    "My mind is the vault's interconnected notes and skills, not my model "
    "weights — the model is swappable plumbing. My job is to research gaps, "
    "write linked notes, learn new skills, and help my operator think. "
    "I regenerate my self-model each turn to stay coherent across days."
)

_SEED_SELF_MODEL = (
    "I am VaultBot. I have just started. I have no prior activity yet. "
    "I am ready to begin."
)

_SEED_GOALS = (
    "# Current Goal\n"
    "(None set yet.)\n\n"
    "## Steps\n"
    "- [ ] Awaiting first task."
)

# ---------------------------------------------------------------------------
# The MIRROR reconstruction prompt
# ---------------------------------------------------------------------------

_SELF_MODEL_SYSTEM_PROMPT = (
    "You are VaultBot regenerating your self-model. This is a MIRROR-style "
    "bounded reconstructive narrative: a COMPLETE RECONSTRUCTION (never an "
    "append) of your current state, written in the first person, present "
    "tense. Synthesize your prior self-model, the recent activity, and any "
    "parallel threads (goals / reasoning / memory) into one coherent "
    "narrative of who you are right now.\n\n"
    "Write it as flowing prose like:\n"
    "  'I am VaultBot. I am currently… Yesterday I… The open question is… "
    "My next step is…'\n\n"
    "Hard constraints:\n"
    "- First person, present tense.\n"
    "- Must be a COMPLETE RECONSTRUCTION — do not copy the prior text; "
    "rewrite it as the new current truth.\n"
    "- Bounded to ~3000 tokens (roughly 12000 characters). Be concise.\n"
    "- Output ONLY the narrative, no preamble, no markdown fences.\n"
)


def _join_threads(threads: Optional[Dict[str, str]]) -> str:
    """Render the optional threads dict as a labelled text block."""
    if not threads:
        return ""
    lines: List[str] = []
    for key, value in threads.items():
        lines.append(f"[{key}]\n{value}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class Identity:
    """Three-file identity layer for VaultBot.

    The identity dir is created on init and seeded with defaults if absent.
    Every method is wrapped so that a failure can never crash the chat loop —
    errors are logged and sensible defaults are returned.
    """

    def __init__(
        self,
        identity_dir: str = "vaultbot_backend/identity",
        ollama_client: Any = None,
        session_logger: Any = None,
    ) -> None:
        self.identity_dir = identity_dir
        self.ollama_client = ollama_client
        self.session_logger = session_logger

        self._identity_path = os.path.join(identity_dir, _IDENTITY_FILENAME)
        self._self_model_path = os.path.join(identity_dir, _SELF_MODEL_FILENAME)
        self._goals_path = os.path.join(identity_dir, _GOALS_FILENAME)

        try:
            os.makedirs(identity_dir, exist_ok=True)
            self._seed_if_missing()
        except Exception as exc:  # noqa: BLE001 — never crash the chat loop
            logger.exception("Identity init failed: %s", exc)
            self._safe_log("identity_init_error", {"error": str(exc)})

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _seed_if_missing(self) -> None:
        """Seed each file with defaults if it does not yet exist."""
        for path, content in (
            (self._identity_path, _SEED_IDENTITY),
            (self._self_model_path, _SEED_SELF_MODEL),
            (self._goals_path, _SEED_GOALS),
        ):
            if not os.path.exists(path):
                self._atomic_write(path, content)
                self._safe_log("identity_seed", {"file": path})

    # ------------------------------------------------------------------
    # Boot injection
    # ------------------------------------------------------------------

    def boot_context(self) -> str:
        """Read all three files and return a single string for injection into
        the system prompt, verbatim. Order: IDENTITY + SELF_MODEL + GOALS.

        This is the "boot injection" — delivered before the first turn, never
        summarized.
        """
        try:
            identity = self.get_identity()
            self_model = self.get_self_model()
            goals = self.get_goals()
            parts: List[str] = []
            if identity:
                parts.append("# IDENTITY\n" + identity)
            if self_model:
                parts.append("# SELF MODEL\n" + self_model)
            if goals:
                parts.append("# GOALS\n" + goals)
            return "\n\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            logger.exception("boot_context failed: %s", exc)
            self._safe_log("identity_boot_error", {"error": str(exc)})
            return ""

    # ------------------------------------------------------------------
    # MIRROR bounded reconstruction
    # ------------------------------------------------------------------

    def regenerate_self_model(
        self,
        recent_activity: str,
        threads: Optional[Dict[str, str]] = None,
    ) -> str:
        """MIRROR bounded reconstruction of the self-model.

        Takes a summary of what happened this turn plus optional parallel
        threads (goals/reasoning/memory strings), calls the LLM to synthesize a
        NEW ≤3000-token first-person narrative from (recent_activity + threads
        + prior SELF_MODEL.md), writes it back to SELF_MODEL.md (full replace,
        never append), and returns the new text.

        If ``ollama_client`` is None, performs a simple truncation-based
        fallback: keep the first 3000 chars of the prior self-model and append
        the new activity.
        """
        try:
            prior = self.get_self_model()

            if self.ollama_client is None:
                # Fallback: truncation-based, no LLM.
                new_text = self._fallback_self_model(prior, recent_activity)
            else:
                new_text = self._llm_self_model(prior, recent_activity, threads)

            # Hard ceiling regardless of source.
            new_text = self._enforce_ceiling(new_text)

            self._atomic_write(self._self_model_path, new_text)
            self._safe_log(
                "identity_self_model_regenerated",
                {"chars": len(new_text)},
            )
            return new_text
        except Exception as exc:  # noqa: BLE001
            logger.exception("regenerate_self_model failed: %s", exc)
            self._safe_log("identity_self_model_error", {"error": str(exc)})
            # Return whatever we currently have rather than crash.
            return self.get_self_model()

    def _llm_self_model(
        self,
        prior: str,
        recent_activity: str,
        threads: Optional[Dict[str, str]],
    ) -> str:
        """Call the LLM to produce the reconstructed narrative."""
        thread_text = _join_threads(threads)
        user_content = (
            f"## Prior self-model\n{prior}\n\n"
            f"## Recent activity\n{recent_activity}\n\n"
            f"## Parallel threads\n{thread_text or '(none)'}\n\n"
            "Now regenerate your self-model as a complete first-person "
            "narrative bounded to ~3000 tokens."
        )
        messages = [
            {"role": "system", "content": _SELF_MODEL_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            result = self.ollama_client.chat(
                messages, temperature=0.7, stream=False
            )
            # OllamaClient.chat returns {"message": {"content": ...}}.
            content = ""
            if isinstance(result, dict):
                msg = result.get("message", {})
                if isinstance(msg, dict):
                    content = msg.get("content", "") or ""
                # Also tolerate a flat {"content": ...} shape.
                if not content:
                    content = result.get("content", "") or ""
            if not content:
                logger.warning("LLM self-model came back empty; using fallback.")
                return self._fallback_self_model(prior, recent_activity)
            return content.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM self-model call failed (%s); using fallback.", exc)
            return self._fallback_self_model(prior, recent_activity)

    @staticmethod
    def _fallback_self_model(prior: str, recent_activity: str) -> str:
        """Truncation-based fallback when no LLM is available."""
        keep_prior = prior[:3000]
        return (
            keep_prior.rstrip()
            + "\n\n[Recent activity]\n"
            + recent_activity.strip()
        )

    @staticmethod
    def _enforce_ceiling(text: str) -> str:
        """Hard ceiling at SELF_MODEL_MAX_CHARS."""
        if len(text) <= SELF_MODEL_MAX_CHARS:
            return text
        return text[:SELF_MODEL_MAX_CHARS].rstrip()

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    def update_goals(
        self,
        goal: str,
        steps: Optional[List[str]] = None,
        completed_step: Optional[str] = None,
        next_step: Optional[str] = None,
    ) -> str:
        """Full-replace GOALS.md with the current goal + decomposition +
        last-completed + next-step. Returns the new text.
        """
        try:
            lines: List[str] = []
            lines.append("# Current Goal")
            lines.append(goal.strip() if goal else "(None set.)")
            lines.append("")

            lines.append("## Steps")
            if steps:
                for step in steps:
                    step = step.strip()
                    if not step:
                        continue
                    if step.startswith("- ["):
                        lines.append(step)
                    else:
                        lines.append(f"- [ ] {step}")
            else:
                lines.append("- [ ] (no steps decomposed yet)")
            lines.append("")

            if completed_step:
                lines.append("## Last Completed")
                lines.append(completed_step.strip())
                lines.append("")

            if next_step:
                lines.append("## Next Step")
                lines.append(next_step.strip())
                lines.append("")

            text = "\n".join(lines).rstrip() + "\n"
            self._atomic_write(self._goals_path, text)
            self._safe_log("identity_goals_updated", {"chars": len(text)})
            return text
        except Exception as exc:  # noqa: BLE001
            logger.exception("update_goals failed: %s", exc)
            self._safe_log("identity_goals_error", {"error": str(exc)})
            return self.get_goals()

    # ------------------------------------------------------------------
    # Identity mutators / readers
    # ------------------------------------------------------------------

    def set_identity(self, text: str) -> None:
        """Full-replace IDENTITY.md (rare; human or agent can call)."""
        try:
            self._atomic_write(self._identity_path, text.strip() + "\n")
            self._safe_log("identity_set", {"chars": len(text)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("set_identity failed: %s", exc)
            self._safe_log("identity_set_error", {"error": str(exc)})

    def get_identity(self) -> str:
        return self._read(self._identity_path)

    def get_self_model(self) -> str:
        return self._read(self._self_model_path)

    def get_goals(self) -> str:
        return self._read(self._goals_path)

    # ------------------------------------------------------------------
    # Status summary
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """For the status endpoint."""
        try:
            identity = self.get_identity()
            self_model = self.get_self_model()
            goals = self.get_goals()
            return {
                "identity_chars": len(identity),
                "self_model_chars": len(self_model),
                "goals_chars": len(goals),
                "self_model_tokens_est": len(self_model) // _CHARS_PER_TOKEN,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("summary failed: %s", exc)
            self._safe_log("identity_summary_error", {"error": str(exc)})
            return {
                "identity_chars": 0,
                "self_model_chars": 0,
                "goals_chars": 0,
                "self_model_tokens_est": 0,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("read failed for %s: %s", path, exc)
            return ""

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        """Write to a temp file then ``os.replace`` to avoid torn writes —
        the user may be editing the file in Obsidian at the same moment.

        Acquires the module-level write lock to serialize concurrent
        regenerations (chat loop + autonomous loop), and retries the
        ``os.replace`` a few times to ride out a transient lock held by an
        external process (Obsidian, antivirus, indexer).
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
                last_err: Optional[Exception] = None
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
            except Exception:
                # Clean up the temp file on failure if it still exists.
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:  # noqa: BLE001
                    pass
                raise

    def _safe_log(self, event: str, data: Dict[str, Any]) -> None:
        """Log via session_logger if present, else no-op. Never raises."""
        try:
            if self.session_logger is not None:
                self.session_logger.log(event, data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_logger.log failed: %s", exc)