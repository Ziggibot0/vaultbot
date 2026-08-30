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
  - Type-checked: each ``TUNABLES.<field>`` is a concrete type to the type
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

from dataclasses import dataclass


@dataclass(frozen=True)
class Tunables:
    """All tunable constants for the VaultBot backend.

    Fields are grouped by subsystem in the order they appear below.
    Every value has a comment explaining what it controls and the unit.
    """

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
    # window size, we never send more than this.
    #
    # History: originally 60K to prevent "2000 t/s but still slow" on local
    # models where 100K+ token prompts made each round sluggish. But that
    # cap was catastrophic for cloud models with 1M context windows: the
    # model was actively working through a complex multi-round task (64
    # tool rounds, making real progress) and the cap kept pruning away
    # context the model needed — old tool results, prior reasoning, file
    # contents — causing it to lose the plot and go in circles. The cap
    # was 16x smaller than the model's actual context window.
    #
    # 800K leaves ~200K for the model's output (thinking + content + tool
    # calls) within a 1M context window. For local models with smaller
    # context (e.g. 32K), the cap still applies as a hard ceiling — the
    # pruning logic in enforce_token_cap activates when total tokens exceed
    # this number, regardless of model context size.
    #
    # Override via env var VAULTBOT_MAX_SEND_TOKENS.
    max_send_tokens: int = 800000

    # ── File-read result cap (chat_handler truncate_tool_result) ─────────
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
    code_run_cap_bytes: int = 65536  # per-stream write cap on disk (64KB)
    code_run_stdout_tail: int = 4000  # bytes of stdout read back
    code_run_stderr_tail: int = 2000  # bytes of stderr read back

    # ── Vault context file count cap ─────────────────────────────────────
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
    session_log_retention_days: int = 30  # delete files older than N days
    session_log_max_file_mb: int = 5  # truncate a single file over this

    # ── Trigger/inhibitor retrieval gate (trigger_store + fused_retrieval) ─
    # How much stronger the inhibitor match must be than the trigger match
    # to drop a note from retrieval.  Conservative start — only drop when
    # the inhibitor clearly dominates.  See trigger_store.py.
    trigger_gate_margin: float = 0.05
    # Minimum consistent feedback signals before Dream-Pass writes a phrase
    # to a note's trigger/inhibitor list.  Prevents single-noisy-turn
    # poisoning (sarcasm, terse "ok").  Mirrors procedure_tracker's
    # FAILURE_THRESHOLD = 3 philosophy but lower (2) because user sentiment
    # is scarcer than procedure pass/fail.
    trigger_evidence_threshold: int = 2
    # Cap on trigger/inhibitor phrases per note to prevent unbounded list
    # growth from endless feedback.
    trigger_max_phrases: int = 15

    # ── Vault-centric synthesis + provenance (2026-08-16) ────────────────
    # The big LLM is a ROUTER/SYNTHESIZER over vault notes. These tunables
    # enforce closed-set citation.
    #
    #   min_retrieval_score: the FUSED similarity score below which a
    #     result is treated as "not really retrieved". Results below this
    #     count as empty. Override via env var VAULTBOT_MIN_RETRIEVAL_SCORE.
    #   max_grounding_retries: how many times finalize_turn may re-enter the
    #     agentic loop to demand a re-cited answer before shipping the answer
    #     with a ⚠️ caution (last resort so the user is never left with no
    #     answer). 1 = one retry round; 0 = soft-fail only (legacy behavior).
    #   ungrounded_sentence_threshold: fraction of answer sentences with no
    #     [[wikilink]] from the allowed-citations set that triggers a grounding
    #     retry. 0.30 = retry if >30% of sentences are uncited. Only applies
    #     to answers >3 sentences (short answers are hard to split cleanly).
    #   legacy_seed_note_cap: per-note char cap for the TOP seed notes in the
    #     legacy build_graph_context path (the common personal-vault case).
    #     Seeds get more body (4000) so the model can synthesize accurately;
    #     non-seed walked nodes keep the tight 900 cap. This is the synthesis-
    #     accuracy lever for non-textbook vaults.
    #   legacy_walked_note_cap: per-note char cap for walked-but-non-seed
    #     nodes in the legacy path.
    #   abstract_extra_drill_cap: char cap for the 2nd/3rd seed drill-down in
    #     the abstract (L0/L1/L2) path. The top seed keeps DRILL_CAP=12000;
    #     seeds 2-3 get this smaller cap so multi-note synthesis isn't
    #     limited to one note's full body.
    min_retrieval_score: float = 0.15
    max_grounding_retries: int = 1
    ungrounded_sentence_threshold: float = 0.30
    legacy_seed_note_cap: int = 4000
    legacy_walked_note_cap: int = 900
    abstract_extra_drill_cap: int = 4000


# The single importable instance. Frozen — do not mutate.
TUNABLES = Tunables()
