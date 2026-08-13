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

    # ── Proactive tool-result aging (chat_handler._age_old_tool_results) ──
    # Age-based stubbing of old tool results, INDEPENDENT of the token cap.
    # The token cap only fires when total tokens exceed 60K; below that, old
    # tool results accumulate full-size across rounds and bloat the prompt
    # the model re-processes every round — distracting it from the current
    # task. This runs EVERY round and stubs tool results older than N rounds
    # back to a 1-line summary, regardless of total token count.
    #   tool_age_rounds_back: how many rounds of tool results to keep verbatim
    #     (default 3 — the model sees the last 3 rounds of results in full)
    #   tool_age_min_chars: only stub results larger than this (tiny results
    #     like {"ok": true} aren't worth stubbing — they're already small)
    #   tool_age_protect_read_tools: if True, never stub code_read /
    #     vault_read_note results (the model may still be referencing them)
    tool_age_rounds_back: int = 3
    tool_age_min_chars: int = 500
    tool_age_protect_read_tools: bool = True

    # ── Hard token cap (chat_handler._enforce_token_cap) ─────────────────
    # Maximum total tokens sent to the big LLM in a single /chat call.
    # This is the GUARANTEED ceiling — regardless of the model's context
    # window size, we never send more than this. On a 128K-context model
    # without this cap, the conversation grew to 100K+ tokens (400K+ chars)
    # because nothing else bounded it: the context budgeter allowed huge
    # vault context, code_read bypassed truncation, and tool results
    # accumulated across rounds. The user saw "2000 t/s but still slow"
    # — the model was chewing through a massive prompt every round.
    # 60K tokens is generous for a multi-round agentic turn while keeping
    # prompt-processing time reasonable. Override via env var.
    max_send_tokens: int = 60000

    # ── File-read result cap (chat_handler truncate_tool_result) ──────────
    # Maximum chars for code_read / vault_read_note tool results before
    # they're truncated. The user wants the model to read the WHOLE file —
    # no truncation except for extremely long files that would actually hurt
    # the model. 120K chars (~30K tokens) is high enough that virtually all
    # vault notes, source files, and textbook sections pass through intact,
    # while still preventing a truly enormous file (e.g. a 500K-char data
    # dump) from blowing the context window. The hard token cap
    # (_enforce_token_cap) is the ultimate backstop. Override via env var
    # VAULTBOT_READ_RESULT_CAP.
    read_result_cap: int = 120000

    # ── Token estimation ─────────────────────────────────────────────────
    # Hard caps on the subprocess stdout/stderr read back into backend RAM,
    # so a verbose child cannot OOM the single backend process.
    code_run_cap_bytes: int = 65536      # per-stream write cap on disk (64KB)
    code_run_stdout_tail: int = 4000     # bytes of stdout read back
    code_run_stderr_tail: int = 2000     # bytes of stderr read back

    # ── Vault context file count cap ──────────────────────────────────────
    # Maximum number of files (L1 cards + MOC + L0 drill-down in the abstract
    # path, or raw notes in the legacy path) that the retrieved vault context
    # can show the model at any given time. This is the hard ceiling on how
    # many distinct files' content appears in the context window — regardless
    # of how many seeds the FUSED retrieval returned or how many nodes the
    # graph walk found. Keeps the context window lean for any model size and
    # saves the user token cost by not flooding the prompt with noise.
    # 15 is the maximum — smaller models benefit from even fewer (the context
    # budgeter + filter_context will further trim by token budget + relevance).
    max_files_in_context: int = 15

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
