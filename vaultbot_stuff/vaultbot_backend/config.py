"""Typed tunables — one source of truth for magic constants.

Every hard-coded threshold/limit/size that was previously a module-level
constant scattered across chat_handler, self_improver, context_budgeter,
identity, small_model_filters, and session_logger lives here now. The
``TUNABLES`` singleton is a frozen dataclass: type-safe, IDE-autocompletable,
and impossible to mutate at runtime.

Env overrides stay where they are (``os.environ.get(...)`` at construction
sites). Only the *default* values move here, so there's one place to see and
tune every knob. Behavior is unchanged — this is a pure relocation refactor.

Why a frozen dataclass (not a dict / not module globals):
  - Type-checked: ``TUNABLES.trivial_max_len`` is an ``int`` to the type
    checker, not ``Any``.
  - Autocompletable: the IDE lists every tunable the moment you type
    ``TUNABLES.``.
  - Immutable: ``frozen=True`` prevents accidental reassignment from
    a hot path.
  - Importable: every module imports the same singleton, so there's no
    risk of two modules drifting on the "same" constant.

If you need a runtime override for a value here, keep the existing
``os.environ.get(NAME, str(TUNABLES.x))`` pattern so the default is
discoverable from this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tunables:
    """All tunable constants for the VaultBot backend.

    Fields are grouped by subsystem in the order they appear below.
    Every value has a comment explaining what it controls and the unit.
    """

    # ── Trivial-turn classifier (chat_handler._classify_trivial) ──────────
    # Short greetings / confirmations routed to the small model directly.
    trivial_max_len: int = 80  # max chars for a message to be "trivial"
    trivial_exact: frozenset[str] = field(default_factory=lambda: frozenset({
        "hi", "hello", "hey", "yo", "sup", "howdy", "greetings",
        "thanks", "thank you", "thx", "ty", "cool", "nice", "great", "awesome",
        "ok", "okay", "sure", "got it", "understood", "makes sense", "perfect",
        "bye", "goodbye", "see ya", "later", "good night",
        "yes", "no", "yep", "nope", "maybe",
        "lol", "haha", "hmm", "huh",
    }))
    trivial_prefixes: tuple[str, ...] = (
        "what can you do", "who are you", "what are you", "introduce yourself",
        "help", "what's your name", "what is your name",
        "good morning", "good afternoon", "good evening",
    )

    # ── Preflight compression (chat_handler._compress_conversation) ───────
    # Hermes-style ratio-based compression. Fires when estimated tokens
    # exceed threshold_ratio × context_window. All values have env overrides
    # at the call site; these are the defaults.
    compression_threshold_ratio: float = 0.50  # fraction of context window
    compression_tail_budget_ratio: float = 0.20  # fraction of threshold for tail
    compression_protect_last_n: int = 6  # min tail messages kept verbatim
    compression_prune_tool_chars: int = 200  # tool results > this get pruned
    compression_min_dropped_to_summarize: int = 3  # min dropped msgs to summarize
    compression_antithrash_count: int = 2  # block after N ineffective passes
    compression_antithrash_min_reduction: float = 0.05  # min token reduction ratio

    # ── Token estimation ─────────────────────────────────────────────────
    # Hard caps on the subprocess stdout/stderr read back into backend RAM,
    # so a verbose child cannot OOM the single backend process.
    code_run_cap_bytes: int = 65536      # per-stream write cap on disk (64KB)
    code_run_stdout_tail: int = 4000     # bytes of stdout read back
    code_run_stderr_tail: int = 2000     # bytes of stderr read back

    # ── Token estimation ─────────────────────────────────────────────────
    # Rough chars-per-token heuristic for English text. Used by
    # context_budgeter and identity (consolidated from both).
    chars_per_token: int = 4

    # ── Small model filters (small_model_filters) ────────────────────────
    small_timeout_seconds: float = 12.0  # small-model call wall-clock timeout

    # ── Session log retention (session_logger.sweep_old_sessions) ────────
    session_log_retention_count: int = 200  # keep newest N files
    session_log_retention_days: int = 30    # delete files older than N days
    session_log_max_file_mb: int = 5        # truncate a single file over this


# The single importable instance. Frozen — do not mutate.
TUNABLES = Tunables()
