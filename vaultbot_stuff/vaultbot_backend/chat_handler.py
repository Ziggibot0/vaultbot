"""Agentic chat loop — Copilot-style, simple.

The model drives. It can use plan_task / update_task to track its own state
(the harness re-injects the working-memory block every round so the model
always sees its todo list). The framework enforces ONE rule: if the model
works 3+ tool rounds without a plan, execution tools are masked until it
calls plan_task. No phase state machine, no auto-advance, no forced
convergence, no consolidation, no step summaries — just the one-rule plan
gate plus the read-loop detector that observes actual tool results.

What we keep:
- sliding window: bound conversation sent to Ollama
- _sanitize_tool_history: convert tool-call rounds to model-safe format
- double-silent failsafe: if the model emits nothing twice, fail loud
- checkpointing: save progress so a crash resumes mid-turn
- answer streaming: answer_chunk / answer_done / thinking events
- per-step RAG: retrieve notes relevant to the current in-progress step
- tool dispatch: execute_agent_tool unchanged (plan_task / update_task /
  set_goal / custom tools / code tools / etc.)

2026-08-02: stripped out all the babysitting. The model is responsible for
planning, tracking, and stopping. We just keep the conversation bounded,
stream the output, and keep the user informed.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from abstract_context import build_abstract_context
from agent_tools import (
    build_system_prompt_briefing, build_tool_list,
)
from chat_checkpoint import snapshot_working_memory

# Leaf-module imports for helpers that were previously deferred-imported
# from main (circular). These are now direct leaf imports — no main dependency.
from chat_helpers import (
    notify_info, notify_problem, run_with_heartbeat, send_progress,
    tool_result_summary, truncate_tool_result,
)
from conversation_state import save_history
from error_types import AgentSilentError
from fastapi import WebSocket
from config import TUNABLES
from procedure_surface import build_procedure_surface, status_allows_execution
from procedure_tracker import interpret_validation_result, parse_procedures_from_results
from services import Services
from conversation_index import build_conversation_context
from small_model_filters import (
    compress_window, dedup_results, expand_query, filter_context,
    rerank_results, rewrite_query_with_history,
)
from task_api import write_partial
from weaving import (
    cross_link_textbooks,
    existing_note_titles,
    link_outbound,
    weave_textbook_notes,
)
from working_memory import TaskList


# ---------------------------------------------------------------------------
# Seen-content deduplication (breaks the "search anxiety" loop)
# ---------------------------------------------------------------------------
# When the model calls vault_search repeatedly with rephrased queries, it gets
# back the same files over and over. This function filters out results the
# model has already seen this turn (via a prior vault_search or code_read),
# and returns the omitted list so the tool result can tell the model exactly
# which files were dropped and why.

def _dedup_seen_results(
    results: list[dict[str, Any]],
    seen: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Annotate search results the model has already seen this turn.

    Returns (annotated, already_seen) where:
      - annotated: ALL results (kept), with already-seen ones marked
      - already_seen: the subset that was already seen (for the "you
        already have these" message)

    Unlike the original version that OMITTED already-seen results, this
    version KEEPS them but annotates them with "already_in_context: true".
    Omitting them caused the model to panic-search more because it got
    empty results and didn't understand why. Showing them with an
    "already seen" annotation lets the model recognize that its searches
    are returning things it already has, which signals it should stop
    searching and write its answer.

    A result is "already seen" if its file_path is in the seen dict with:
      - source "vault_search" or "initial_context" (already in context)
      - source "code_read" with lines=None (full file read)
      - source "code_read" with lines=(s,e) (partial — annotated with
        which lines were already read)
    """
    annotated: list[dict[str, Any]] = []
    already_seen: list[dict[str, Any]] = []
    for r in results:
        fp = r.get("file_path", "")
        if not fp:
            annotated.append(r)
            continue
        entry = seen.get(fp)
        if entry is None:
            # Never seen — keep it as-is.
            annotated.append(r)
            continue
        # Already seen — annotate it but KEEP it so the model can see
        # that its search returned things it already has.
        r = dict(r)
        if entry.get("source") == "code_read" and entry.get("lines") is not None:
            _sl, _el = entry["lines"]
            r["already_read_lines"] = f"{_sl}-{_el}"
            r["already_in_context"] = True
        else:
            r["already_in_context"] = True
        annotated.append(r)
        already_seen.append(r)
    return annotated, already_seen


# ---------------------------------------------------------------------------
# Trivial-turn shortcut (Phase 7: skip big model for simple messages)
# ---------------------------------------------------------------------------
# Simple greetings, thanks, and meta-questions ("hi", "thanks!", "what can
# you do?") don't need the full agentic loop with the cloud model. This
# classifier routes them to the small model directly, saving cloud tokens
# and giving an instant response. Conservative: if in doubt, fall through
# to the big model. False negatives cost a few tokens; false positives cost
# answer quality.

# Patterns that indicate a trivial turn. Must be SHORT and match the whole
# message (case-insensitive, stripped). Keywords are matched as whole words.
# The exact-set and prefix-tuple live in config.TUNABLES (single source of
# truth); local aliases keep the call sites readable.
_TRIVIAL_EXACT = TUNABLES.trivial_exact
_TRIVIAL_PREFIXES = TUNABLES.trivial_prefixes
_TRIVIAL_MAX_LEN = TUNABLES.trivial_max_len


def _classify_trivial(
    user_message: str,
    conversation_history: list[dict[str, Any]],
    wm: TaskList | None,
) -> bool:
    """Deterministically classify whether a turn is trivial enough to skip
    the big cloud model and route directly to the small model.

    Returns True only if ALL of:
    - The message is short (< _TRIVIAL_MAX_LEN chars).
    - The message matches a greeting, confirmation, thanks, or meta-question
      pattern (exact match or prefix match).
    - There is no active plan in working memory.
    - There are no recent tool calls in conversation history (last 4 msgs).

    Conservative: anything uncertain falls through to the big model.
    """
    msg = user_message.strip().lower()
    if not msg or len(msg) > _TRIVIAL_MAX_LEN:
        return False

    # Exact match check.
    if msg in _TRIVIAL_EXACT:
        pass  # trivial
    elif any(msg.startswith(p) for p in _TRIVIAL_PREFIXES):
        pass  # trivial
    else:
        return False

    # No active plan — a trivial message during an active task might be
    # a continuation that needs the big model's context.
    if wm is not None and wm.has_plan():
        return False

    # No recent tool calls — if the last few messages include tool activity,
    # the user might be reacting to tool results and needs full context.
    if conversation_history:
        for _m in conversation_history[-4:]:
            if isinstance(_m, dict):
                if _m.get("tool_calls") or _m.get("role") == "tool":
                    return False

    return True


# ---------------------------------------------------------------------------
# Stable system-prompt compression cache (Phase 5: cost reduction)
# ---------------------------------------------------------------------------
# The identity block (~3K tokens) + briefing (~2-5K tokens) are sent verbatim
# every round of the agentic loop. Over a 15-round turn that's ~75K tokens of
# stable text re-billed 15 times. This cache compresses the stable parts
# once per session via the small model and reuses the compressed version for
# all rounds. Keyed by hash of the input so identity file edits trigger
# re-compression. Fail-safe: any error falls back to the original text.
_stable_prompt_cache: dict[str, str] = {}


def _compress_stable_prompt(
    identity_context: str,
    briefing: str,
    session_logger: Any = None,
) -> str:
    """Compress the stable system-prompt components via the small model.

    The identity block and the system prompt briefing change rarely within a
    session but are sent to the big (cloud) model on every round of the
    agentic loop. Running them through the small model once to produce a
    condensed version (target ~40% reduction) saves ~4-6K tokens × every
    round of cloud billing.

    The compression is extractive — it must preserve ALL factual content
    (identity facts, tool names, mission, goals, instructions) and remove
    only redundancy and verbose phrasing. The small model (qwen3.5:0.8b) is
    adequate for this with a strict "preserve all facts" prompt.

    Caching: the result is cached by a hash of (identity + briefing) so if
    the identity files change mid-session, a new hash triggers re-compression.

    Fail-safe: if the small model is unavailable, the compression produces
    something too short (<20% of original) or too long (>90%), or any error
    occurs, the original uncompressed text is returned.
    """
    original = identity_context + "\n\n" + briefing
    if not original.strip():
        return original

    import hashlib
    _key = hashlib.blake2b(original.encode(), digest_size=16).hexdigest()

    # Cache hit — reuse the compressed version.
    if _key in _stable_prompt_cache:
        if session_logger:
            session_logger.log("system_prompt_compressed", {
                "cached": True,
                "original_chars": len(original),
                "compressed_chars": len(_stable_prompt_cache[_key]),
            })
        return _stable_prompt_cache[_key]

    # Try the small model for compression.
    try:
        from llm_client import get_small_client
        from small_model_filters import _breaker_tripped, _breaker_trip, _breaker_reset
        if _breaker_tripped("compress_prompt"):
            return original
        client = get_small_client(session_logger)
        if client is None:
            return original  # no small model → use original

        prompt = (
            "Compress the following system prompt to approximately 40% of "
            "its length. Preserve ALL factual content: identity facts, tool "
            "names, mission, goals, and instructions. Remove only redundancy "
            "and verbose phrasing. Return only the compressed text, no "
            "preamble.\n\n"
            f"{original[:16000]}")
        resp = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2, stream=False, think=False, max_predict=2048)
        text = ""
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                text = resp.get("response", "") or resp.get("content", "")
        text = (text or "").strip()

        if not text or len(text) < 20:
            return original

        # Guard: if compression didn't help enough (<20%) or was too aggressive
        # (>90% of original), fall back to original.
        _ratio = len(text) / len(original)
        if _ratio < 0.20 or _ratio > 0.90:
            if session_logger:
                session_logger.log("system_prompt_compression_skipped", {
                    "ratio": round(_ratio, 2),
                    "original_chars": len(original),
                    "compressed_chars": len(text),
                })
            return original

        _breaker_reset("compress_prompt")
        _stable_prompt_cache[_key] = text
        if session_logger:
            session_logger.log("system_prompt_compressed", {
                "cached": False,
                "original_chars": len(original),
                "compressed_chars": len(text),
                "ratio": round(_ratio, 2),
            })
        return text
    except Exception as e:  # noqa: BLE001 — compression is best-effort; the original prompt passes through unchanged on failure
        try:
            from small_model_filters import _breaker_trip
            _breaker_trip("compress_prompt")
        except Exception as be:  # noqa: BLE001 — breaker trip is best-effort; log but don't mask the original error
            if session_logger:
                session_logger.log("breaker_trip_failed",
                                   {"breaker": "compress_prompt", "error": str(be)})
        if session_logger:
            session_logger.log("system_prompt_compression_failed",
                               {"error": str(e)})
        return original


def _deterministic_procedure_hint(
    results: list[dict],
    proc_index: dict[str, dict[str, Any]] | None,
    user_message: str,
) -> str:
    """Deterministically pick the best-matching procedure for the query.

    Replaces the small-model procedure-routing hint. FUSED retrieval already
    ranked these same procedure notes by embedding + graph + backlink
    similarity to the query, so the best hint is the highest-scored surfaced
    procedure that is executable (not flagged). This reuses a score already
    computed — zero LLM calls, zero new embeddings, never worse than the
    small model's pick (it was choosing from the same surface set).

    Skipped for trivial/greeting messages (no procedure is the right answer
    there) and for flagged procedures (cannot run).
    """
    if not results:
        return ""
    # Skip greetings/trivial messages — no procedure is the right hint.
    _msg_low = user_message.strip().lower()
    _trivial = _msg_low in {"hi", "hello", "hey", "yo", "sup", "ok", "thanks", "thank you"}
    if _trivial or len(_msg_low) < 5:
        return ""

    _best_stem = ""
    _best_score = -1.0
    for r in results:
        if not isinstance(r, dict):
            continue
        fp = r.get("file_path", "")
        if not fp:
            continue
        stem = Path(fp).stem
        # Only consider actual procedures — the proc_index is the
        # authoritative map of stem -> {path, frontmatter}. A non-procedure
        # note with a higher FUSED score must not be hinted as a procedure.
        if not proc_index or stem not in proc_index:
            continue
        fm = proc_index[stem].get("frontmatter") or {}
        status = (fm.get("status") or "").strip().lower()
        # Skip flagged procedures — they're blocked from execution.
        if status == "flagged":
            continue
        score = float(r.get("score", 0.0) or 0.0)
        if score > _best_score:
            _best_score = score
            _best_stem = stem

    # Require a minimum similarity so we don't hint a procedure for a query
    # it's only weakly related to (the small model returned 'none' in that
    # case; we mirror that here). FUSED scores are normalized to [0,1].
    if _best_stem and _best_score >= 0.20:
        return _best_stem
    return ""


def _small_model_procedure_hint(
    user_message: str,
    procedure_lines: list[str],
    session_logger: Any = None,
) -> str:
    """Use the small model to pre-classify which procedures match the task.

    Given the user message and the procedure surface lines (name + description
    + status), the small model picks the best-matching procedure name. The
    framework validates that the returned name exists in the real procedure
    list — a hallucinated name is silently dropped. The big model still makes
    the final execute_procedure call; this is just a hint that reduces its
    reasoning load (it doesn't have to read and evaluate each procedure line).
    """
    if not procedure_lines:
        return ""
    try:
        from llm_client import get_small_client_or_big
        client = get_small_client_or_big(session_logger)
        # Extract just the procedure names from the surface lines for the
        # prompt so the small model picks from a known set.
        names = []
        for line in procedure_lines:
            # Surface lines look like "- Verify-Claims — desc [status]"
            stripped = line.lstrip("- ").strip()
            if stripped:
                name = stripped.split("—")[0].split("[")[0].strip()
                if name:
                    names.append(name)
        if not names:
            return ""

        prompt = (
            "Given the user's message and a list of procedures, determine "
            "which procedure (if any) is most relevant. Output ONLY the "
            "procedure name, or 'none' if none are relevant.\n\n"
            f"User message: {user_message[:300]}\n\n"
            f"Procedures:\n" + "\n".join(f"- {n}" for n in names) + "\n\n"
            "Most relevant procedure:")
        resp = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.1, stream=False)
        text = ""
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                text = resp.get("response", "") or resp.get("content", "")
        text = (text or "").strip().split("\n")[0].strip()
        # Guard: only accept a name that's in the real procedure list.
        if text and text.lower() != "none":
            _text_low = text.lower()
            for n in names:
                if n.lower() == _text_low:
                    return n  # return the real-cased name
            # No exact match — hallucinated name. Drop it.
            if session_logger:
                session_logger.log("procedure_hint_hallucinated", {
                    "returned": text,
                    "real_names": names,
                })
        return ""
    except Exception:
        if session_logger:
            session_logger.log("procedure_hint_error", {"error": "exception in _match_procedure"})
        return ""


def _small_model_query(goal: str, step_content: str, session_logger: Any = None) -> str:
    """Deterministically build a search query from the step description.

    Replaces the old small-model call that turned a step description into a
    search query. The small model was just rephrasing the step text — a
    bounded rewrite that doesn't need LLM reasoning. The FUSED retriever
    already does keyword extraction and topic focusing internally, so it
    doesn't need a pre-rephrased query. The raw step text (prefixed with the
    goal for context) is a better query because it preserves specific terms
    the small model would often drop.

    Zero LLM calls. The old fallback path (when the small model was
    unavailable) was literally this — ``(goal + " " + step_content).strip()``
    — and it worked fine.
    """
    query = (goal + " " + step_content).strip()
    if session_logger:
        session_logger.log("deterministic_step_query", {
            "query": query[:120],
        })
    return query


def _small_model_digest(result: dict[str, Any], session_logger: Any = None) -> dict[str, Any]:
    """Use the small model to digest a non-code tool result into a compact summary.

    Extends _digest_code_read (which only handles .py files) to any tool result.
    The small model writes a 2-3 sentence structural summary: what the file
    contains, key sections, and what the agent should know. Hallucination
    guard: every word in the summary must appear in the source content. If
    the summary fails the guard, fall back to a deterministic truncation.
    """
    content = result.get("content", "")
    if not isinstance(content, str) or len(content) < 200:
        return result  # too short to need digesting

    from small_model_filters import _breaker_tripped, _breaker_trip, _breaker_reset
    if _breaker_tripped("digest"):
        return result

    try:
        from llm_client import get_small_client_or_big
        client = get_small_client_or_big(session_logger)
        file_path = result.get("file_path", "?")
        prompt = (
            "Summarize the following content in 2-3 sentences. "
            "State what it is, its key sections, and the main points. "
            "Use ONLY words that appear in the source — do NOT add "
            "information that isn't in the text below.\n\n"
            f"File: {file_path}\n\n"
            f"Content:\n{content[:6000]}\n\nSummary:")
        resp = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2, stream=False, think=False, max_predict=256)
        text = ""
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                text = resp.get("response", "") or resp.get("content", "")
        text = (text or "").strip()

        if not text or len(text) < 20:
            return result

        # Hallucination guard: check that the summary's content words all
        # appear in the source. If the small model invented facts, the word
        # overlap will be low and we fall back to deterministic truncation.
        _src_words = set(content.lower().split())
        _sum_words = set(text.lower().split())
        _stop = {"the", "a", "an", "is", "are", "was", "were", "be", "to",
                 "of", "in", "on", "at", "and", "or", "it", "this", "that",
                 "for", "with", "as", "by", "its", "has", "have", "from",
                 "which", "not", "but", "can", "will", "do", "does", "did",
                 "you", "your", "we", "our", "they", "their", "he", "she"}
        _content_words = _sum_words - _stop
        if _content_words:
            _overlap = len(_content_words & _src_words) / len(_content_words)
            if _overlap < 0.6:
                if session_logger:
                    session_logger.log("small_model_digest_hallucination", {
                        "file_path": file_path,
                        "overlap": round(_overlap, 2),
                    })
                return result  # fall back — don't use the summary

        new = dict(result)
        new["content"] = (
            f"[DIGESTED by small model — full body omitted to protect "
            f"reasoning budget]\n{text}\n\n"
            f"The raw body is NOT included. If you need specific details, "
            f"call the tool again with narrower parameters.")
        new["digested"] = True
        new["original_chars"] = len(content)
        _breaker_reset("digest")
        return new
    except Exception:
        _breaker_trip("digest")
        return result  # fall back to raw result on any error


def _tool_actually_wrote(tool_name: str, result: Any) -> bool:
    """Determine whether a tool call actually changed something.

    This is the core fix for the read-loop detector: a "write" tool that
    failed (execute_procedure returning 0 steps, vault_safe_write rejected,
    code_run with an error) is NOT a successful write — it's a failed read.
    Counting it as a write resets the read-loop detector and lets the model
    loop forever calling a broken tool.

    Returns True only when the tool produced a real, successful change.
    """
    if not isinstance(result, dict):
        return False
    if tool_name == "execute_procedure":
        return result.get("steps_executed", 0) > 0
    if tool_name in ("vault_safe_write", "safe_write", "js_safe_write"):
        return result.get("status") == "ok" and not result.get("error")
    if tool_name in ("vault_append",):
        return not result.get("error")
    if tool_name in ("vault_delete",):
        return not result.get("error")
    if tool_name == "code_run":
        return result.get("exit_code", -1) == 0 and not result.get("error")
    if tool_name == "tool_create":
        return result.get("status") == "ok" and not result.get("error")
    if tool_name == "vault_research":
        return result.get("source_count", 0) > 0 or result.get("note_path")
    # Unknown write tool — be conservative: treat as write only if no error.
    return not result.get("error")


def _apply_sliding_window(
    conversation: list[dict[str, Any]],
    window_size: int = 0,
    context_window_tokens: int = 0,
) -> list[dict[str, Any]]:
    """Bound the conversation sent to the LLM to the last N messages.

    Replaces lossy compaction (LLM summarization) with a deterministic slice.
    The first 2 messages (system prompt + vault context) are always kept;
    the last ``window_size`` messages are kept; everything in between is
    dropped. The full conversation is on disk in chat notes
    (``vaultbot_stuff/Memory/Chat/Chat-*.md``) and retrievable via ``vault_search``.

    Tool-call-pair safety: if the window boundary lands on a ``tool`` message
    whose parent ``assistant`` message (with ``tool_calls``) would be outside
    the window, walk the boundary backward until it lands on a non-tool
    message. This prevents orphaned tool results that break the Ollama tool
    protocol.

    Token-budget guard: when ``context_window_tokens`` is provided, the
    window is skipped entirely if the conversation's estimated token count
    is under 60% of the context window.  This prevents the window from
    eating context on models with large context windows (e.g. 1M-token
    cloud models) where truncation at 40 messages destroys useful history
    for no reason.  The 60% threshold leaves headroom for the model's output.

    See [[Sliding-Window-Conversation-Trail-Tools-as-Procedures-Spec]].
    """
    if window_size <= 0:
        # Default 40 (was 100, originally 20). 100 messages ~ 31k tokens of
        # tool chatter — that bloats every turn's prompt and slows TTFT on a
        # local 30B model. 40 messages ~ 20 tool rounds covers multi-round
        # agentic loops; older context lives in chat notes on disk and is
        # retrievable via vault_search. Override with VAULTBOT_SLIDING_WINDOW.
        window_size = int(os.getenv("VAULTBOT_SLIDING_WINDOW", "40"))
    if len(conversation) <= window_size + 2:
        return conversation

    # Token-budget guard: skip truncation when there is plenty of headroom.
    # Estimates tokens as ~4 chars/token (rough but sufficient for the
    # guard decision).  Only truncate when we're approaching the limit.
    if context_window_tokens > 0:
        _est_tokens = sum(
            len(str(m.get("content", "") or ""))
            for m in conversation if isinstance(m, dict)
        ) // 4
        if _est_tokens < context_window_tokens * 0.60:
            return conversation

    head = conversation[:2]
    split_point = len(conversation) - window_size

    # Walk backward past tool messages so we don't split a tool-call pair.
    while split_point > 2 and isinstance(conversation[split_point], dict) \
            and conversation[split_point].get("role") == "tool":
        split_point -= 1

    # Phase 5: small-model conversation compression — summarize dropped
    # messages instead of losing them entirely. Fail-safe: on any error,
    # messages are dropped as before (today's behavior).
    dropped = conversation[2:split_point]
    if len(dropped) > 3:
        summary = compress_window(dropped, session_logger=None)
        if summary:
            return head + [{"role": "system",
                "content": f"[PRIOR ROUNDS SUMMARY]\n{summary}"}] \
                + conversation[split_point:]

    return head + conversation[split_point:]


def _enforce_history_budget(
    conversation: list[dict[str, Any]],
    budget_tokens: int = 0,
    session_logger: Any = None,
) -> list[dict[str, Any]]:
    """Cap the history tokens sent to the big model per round.

    The sliding window (_apply_sliding_window) uses a 60%-of-context-window
    guard that effectively never truncates on cloud models with large context
    windows (e.g. glm-5.2:cloud with 1M tokens). This means history grows
    unbounded and every round re-bills the entire pile to the cloud provider.

    This function is the tight second pass: it caps the HISTORY portion
    (everything after the first 2 system+context messages) to a configurable
    token budget. When the history exceeds the budget, it drops the oldest
    messages (preserving tool-call pairs) and injects a small-model
    compress_window() summary of the dropped content as a system message.

    The first 2 messages (system prompt + vault context) are ALWAYS kept —
    they are budgeted separately by the ContextBudgeter and contain the
    reasoning highway the model needs.

    Args:
        conversation: The full conversation list (system, context, history...).
        budget_tokens: Max tokens for history. 0 = read from env
            VAULTBOT_BIG_MODEL_HISTORY_BUDGET (default 8000).
        session_logger: For logging the truncation event.

    Returns:
        The conversation with history capped to the budget.
    """
    if budget_tokens <= 0:
        budget_tokens = int(os.getenv("VAULTBOT_BIG_MODEL_HISTORY_BUDGET", "8000"))
    if budget_tokens <= 0 or len(conversation) <= 3:
        return conversation

    head = conversation[:2]  # system prompt + vault context (always kept)
    history = conversation[2:]

    # Estimate tokens for the history portion only.
    _est_tokens = sum(
        len(str(m.get("content", "") or ""))
        for m in history if isinstance(m, dict)
    ) // 4

    if _est_tokens <= budget_tokens:
        return conversation

    # Walk backward from the end, keeping messages until we hit the budget.
    # This preserves the most recent context (which is most relevant).
    kept: list[dict[str, Any]] = []
    running_tokens = 0
    for msg in reversed(history):
        msg_tokens = len(str(msg.get("content", "") or "")) // 4 + 1
        if running_tokens + msg_tokens > budget_tokens:
            break
        kept.insert(0, msg)
        running_tokens += msg_tokens

    # Tool-call-pair safety: if the first kept message is a 'tool' message
    # whose parent assistant (with tool_calls) was dropped, drop it too.
    while kept and isinstance(kept[0], dict) and kept[0].get("role") == "tool":
        kept.pop(0)

    if len(kept) == len(history):
        return conversation  # nothing was dropped

    dropped = history[:len(history) - len(kept)]
    if session_logger:
        session_logger.log("history_budget_truncated", {
            "history_msgs_before": len(history),
            "history_msgs_after": len(kept),
            "dropped_msgs": len(dropped),
            "est_tokens_before": _est_tokens,
            "est_tokens_after": running_tokens,
            "budget_tokens": budget_tokens,
        })

    # Summarize dropped messages via the small model (best-effort).
    summary_msg: list[dict[str, Any]] = []
    if len(dropped) > 3:
        summary = compress_window(dropped, session_logger=session_logger)
        if summary:
            summary_msg = [{"role": "system",
                "content": f"[PRIOR ROUNDS SUMMARY]\n{summary}"}]

    return head + summary_msg + kept


def _digest_code_read(result: dict[str, Any]) -> dict[str, Any]:
    """Compress a code_read tool result into a compact structural digest.

    A thinking model (nemotron, o1, qwq, etc.) handed a ~35KB raw file dump
    tends to ECHO it char-by-char inside its reasoning field, burning its
    whole output budget before it ever writes the answer — the "cut out"
    failure seen in session 9450d6ad. This digest keeps the ACTIONABLE parts
    (which file, how big, what it imports, its top-level structure) and drops
    the raw body, so the model reasons over the gist instead of echoing text.

    The full content stays retrievable: the digest tells the model to call
    code_read again with a narrower line range if it needs a specific section.

    Returns a NEW dict (does not mutate the result in place beyond replacing
    the heavy `content` field with the digest + a short preview).
    """
    content = result.get("content", "")
    file_path = result.get("file_path", "?")
    total = result.get("total_lines", "?")
    start = result.get("start_line", "?")
    end = result.get("end_line", "?")

    lines = content.splitlines()
    # Pull the import/include block (the most useful structural signal for
    # "I read my own code" — what this file depends on).
    imports = [l for l in lines if l.strip().startswith(
        ("import ", "from ", "require(", "#include", "using "))]
    # Pull top-level definition names (def/class/function) for structure.
    import re as _re
    defs = []
    for l in lines:
        m = _re.match(r"\s*(?:def|class|function|const|async def)\s+([A-Za-z_][\w]*)", l)
        if m:
            defs.append(m.group(1))

    digest_lines = [
        "[DIGESTED code_read — full body omitted to protect your reasoning budget]",
        f"file: {file_path}",
        f"size: {total} total lines; you read lines {start}–{end} ({len(lines)} lines).",
    ]
    if imports:
        digest_lines.append(f"imports ({len(imports)}): " + "; ".join(import_.strip()[:70] for import_ in imports[:12]))
    if defs:
        digest_lines.append(f"definitions ({len(defs)}): " + ", ".join(defs[:25]))
    digest_lines.append(
        "The raw body is NOT included. If you need a specific section, call "
        "code_read again with a tight start_line/end_line around that symbol "
        "instead of re-reading the whole file.")

    new = dict(result)
    new["content"] = "\n".join(digest_lines)
    new["digested"] = True
    new["original_chars"] = len(content)
    return new


def _sanitize_tool_history(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert tool-call messages to a format the model can handle.

    glm-5.2:cloud via Ollama returns completely empty when the conversation
    history contains assistant messages with ``tool_calls`` or ``tool`` role
    messages. This function creates a sanitized copy where:

    - Assistant messages with ``tool_calls``: the ``tool_calls`` field is
      stripped. The assistant's ``content`` is kept as-is (the model's text).
      If content is empty, a minimal placeholder is used.
    - ``tool`` role messages are converted to ``system`` role messages that
      describe both the tool call AND its result in one message:
      ``[Tool call: name({args}) returned: {result}]``
      This pairs the tool call (from the preceding assistant's tool_calls)
      with the tool result (from the tool message) so the model sees the
      full context of what it called and what it got back.

    The original conversation is NOT modified — the loop logic still needs
    the ``tool_calls`` field for round tracking. Only the copy sent to
    Ollama is sanitized.

    This is the fix for "VaultBot stops after a few tool calls" — the model
    goes empty on every round after the first tool call because it sees
    ``tool_calls`` in the history.
    """
    # Build a lookup of tool_call_id → tool_call args from assistant messages
    # so we can pair tool results with their originating tool calls.
    tool_call_lookup: dict[str, dict] = {}
    for msg in conversation:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id:
                    tool_call_lookup[tc_id] = tc

    sanitized = []
    for msg in conversation:
        role = msg.get("role", "")
        # Tool role messages → system role with full call+result info
        if role == "tool":
            tool_content = msg.get("content", "")
            tool_name = msg.get("tool_name", "tool")
            tool_call_id = msg.get("tool_call_id", "")
            # Look up the original tool call args
            tc = tool_call_lookup.get(tool_call_id, {})
            fn = tc.get("function", {}) if tc else {}
            args_raw = fn.get("arguments", "{}") if fn else "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                args_str = json.dumps(args, default=str)[:300]
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                args_str = str(args_raw)[:300]
            # Build the combined system message
            result_text = tool_content
            if len(result_text) > 2000:
                result_text = result_text[:2000] + "...[truncated]"
            system_content = (
                f"[Tool call: {tool_name}({args_str}) returned: {result_text}]"
            )
            # Merge multiple tool results into one system message if the
            # previous sanitized message is also a tool-result system msg.
            if sanitized and sanitized[-1].get("role") == "system" \
                    and str(sanitized[-1].get("content", "")).startswith("[Tool call:"):
                sanitized[-1]["content"] += "\n" + system_content
            else:
                sanitized.append({"role": "system", "content": system_content})
            continue
        # Assistant messages with tool_calls → strip tool_calls, keep content
        if role == "assistant" and msg.get("tool_calls"):
            content = msg.get("content", "") or ""
            thinking = msg.get("thinking", "") or ""
            # Keep the model's actual text. If empty (tool-only round), use
            # empty string — the system message with the tool result provides
            # the context. Using a placeholder like "(working...)" or "." caused
            # the model to echo it back in subsequent rounds.
            if not content.strip():
                content = ""
            new_msg = {"role": "assistant", "content": content}
            if thinking:
                new_msg["thinking"] = thinking
            sanitized.append(new_msg)
            continue
        # All other messages pass through (strip any stray tool_calls)
        new_msg = dict(msg)
        new_msg.pop("tool_calls", None)
        sanitized.append(new_msg)
    return sanitized


async def handle_chat(svc: Services, websocket: WebSocket,
                     user_message: str, session_logger) -> None:
    """Agentic chat: the LLM reasons over the vault, calls tools when it hits
    a gap, and produces a grounded answer. The model drives — no framework
    enforcement, no phases, no auto-planning.
    """
    session_logger.log("chat_begin", {"user_message": user_message})

    # Working memory: per-session structured task list. The model writes a
    # plan via plan_task and updates it via update_task; the harness re-injects
    # the list into the system prompt every round so the model always sees
    # "what's done, what's next." One TaskList per websocket connection,
    # reset on /new. THE MODEL OWNS THIS — the framework never auto-advances
    # or force-completes anything.
    if not hasattr(websocket, "working_memory") or websocket.working_memory is None:
        websocket.working_memory = TaskList()
    wm = websocket.working_memory
    # A new user message is a NEW turn. We do NOT clear the working-memory
    # plan automatically — the plan persists across turns so the model can
    # handle interruptions and follow-up questions without losing its
    # place. The plan is cleared when:
    #   - the model explicitly calls plan_task with a new plan (set_plan
    #     replaces the old list), or
    #   - the model completes all tasks and we detect all_done(), or
    #   - the user types /new (ws.py clears it).
    # If the prior plan is fully done, clear it so the model starts fresh.
    if wm.has_plan() and wm.all_done():
        session_logger.log("wm_plan_cleared_completed", {
            "previous_goal": wm.goal[:100],
            "completed_steps": sum(1 for t in wm.tasks if t.status == "completed"),
            "total_steps": len(wm.tasks),
        })
        wm.clear()

    # Chat-loop checkpoint/resume: if a prior turn was interrupted mid-loop
    # and left a fresh checkpoint, resume it — restore the working-memory plan
    # and tell the model what it already did so it doesn't re-run tools.
    # Cleared on normal completion and /new.
    _cp = getattr(svc, "chat_checkpointer", None)
    _resumed_tool_history: list = []
    if _cp is not None:
        try:
            _prior = _cp.load()
            if _prior and _prior.get("user_message") == user_message:
                _resumed_tool_history = _prior.get("tool_history", []) or []
                _wm_snap = _prior.get("working_memory") or {}
                if _wm_snap and not wm.has_plan():
                    try:
                        wm.restore_snapshot(_wm_snap)
                    except Exception as e:  # noqa: BLE001 — best-effort
                        session_logger.log("wm_restore_failed", {"error": str(e)})
                session_logger.log("chat_checkpoint_resumed", {
                    "round_idx": _prior.get("round_idx", 0),
                    "tools_already_run": len(_resumed_tool_history),
                })
        except Exception as e:  # noqa: BLE001 — best-effort
            session_logger.log("chat_checkpoint_resume_failed", {"error": str(e)})

    # Chat-priority: pause the autonomous researcher so it doesn't compete
    # with this interactive turn for the Ollama GPU. Resumed in the finally
    # block so it always clears.
    svc.autonomous_researcher.pause_for_chat()
    try:

        # Calibration: detect if this message is a correction of the previous
        # answer. Corrections are ground truth for calibrating quality gates.
        try:
            _prev_history = getattr(websocket, "conversation_history", None)
            _prev_answer = None
            if _prev_history:
                for _msg in reversed(_prev_history):
                    if _msg.get("role") == "assistant" and _msg.get("content"):
                        _prev_answer = _msg["content"]
                        break
            if _prev_answer and svc.calibration_tracker.detect_correction(user_message, _prev_answer):
                _ftype = svc.calibration_tracker.classify_failure(user_message, _prev_answer)
                svc.calibration_tracker.log_correction(
                    user_message, _prev_answer, failure_type=_ftype)
                session_logger.log("correction_detected", {"failure_type": _ftype})
        except Exception as e:  # noqa: BLE001 — best-effort
            session_logger.log("correction_detection_failed", {"error": str(e)})

        await svc.manager.send_personal_message(json.dumps({"type": "status", "content": "Searching vault..."}), websocket, session_logger=session_logger)
        loop = asyncio.get_event_loop()
        chat_start_time = loop.time()  # for vault_changed file scan

        # Keep the in-memory vault graph current with disk before retrieval.
        try:
            _t_graph = loop.time()
            await loop.run_in_executor(None, svc.vault_graph.refresh)
            session_logger.log("graph_refreshed", {
                "node_count": len(svc.vault_graph.nodes),
                "duration_ms": (loop.time() - _t_graph) * 1000,
            })
        except Exception as e:  # noqa: BLE001
            session_logger.log_exception(e, context="graph_refresh")

        # RAG: retrieve vault context relevant to the user's message.
        # Phase 3: conversation-aware query rewriting — rewrite the user's
        #   query using conversation context so follow-ups like "what was
        #   that thing?" resolve to the actual topic. Fail-safe: returns
        #   the original message on any failure.
        # Phase 2: small-model query expansion (fail-safe — always includes
        #   the raw user message, so retrieval is never worse than today).
        # Phase 1: small-model reranking (over-fetch k=15, rerank down to 5
        #   via the Smart-Vault-Search procedure; fail-safe — falls back to
        #   FUSED order on any error).
        t0 = loop.time()
        _rewritten_query = user_message  # default: no rewrite
        try:
            # Conversation-aware query rewriting: resolve references to prior
            # conversation so retrieval finds the right notes.
            _history = getattr(websocket, "conversation_history", [])
            _rewritten_query = await loop.run_in_executor(
                None, rewrite_query_with_history,
                user_message, _history, session_logger)
            queries = [_rewritten_query]
            if svc.small_client:
                _expanded = expand_query(
                    svc.small_client, _rewritten_query, session_logger)
                # Always include the original user message so retrieval is
                # never worse than baseline.
                queries = list(dict.fromkeys(
                    [_rewritten_query] + _expanded + [user_message]))
            # Run all query retrievals concurrently. Each retrieve() is a
            # blocking call scheduled on the default executor; gathering them
            # turns N sequential round-trips into one parallel wave, cutting
            # retrieval latency ~N× (3 queries → ~3×). A single heartbeat
            # label covers the whole wave so the UI stays responsive.
            all_results: list[dict] = []
            if len(queries) <= 1:
                fused_result = await run_with_heartbeat(
                    svc, websocket, "retrieving vault",
                    svc.fused_retriever.retrieve, queries[0], 15, 1)
                _r = fused_result.get("results", []) if isinstance(fused_result, dict) else (fused_result or [])
                all_results.extend(_r)
            else:
                _qlabel = "retrieving vault"
                await send_progress(svc, websocket, _qlabel, {})
                try:
                    gathered = await asyncio.gather(*[
                        loop.run_in_executor(
                            None, svc.fused_retriever.retrieve, q, 15, 1)
                        for q in queries[:3]
                    ])
                    for _fr in gathered:
                        _r = _fr.get("results", []) if isinstance(_fr, dict) else (_fr or [])
                        all_results.extend(_r)
                finally:
                    await send_progress(svc, websocket, _qlabel + "_done", {})
            if len(queries) > 1:
                results = dedup_results(all_results)
            else:
                results = all_results
            # Phase 1: deterministic reranking (embedding cosine similarity).
            # No longer gated on svc.small_client — the reranker uses FAISS
            # vector reconstruction, not an LLM call.
            if len(results) > 5:
                results = await rerank_results(
                    svc, user_message, results, k=5,
                    session_logger=session_logger)
            else:
                results = results[:5]
        except Exception as e:  # noqa: BLE001
            session_logger.log_exception(e, context="fused_retriever.retrieve")
            await notify_problem(svc, websocket, e,
                context={"category": "retrieval_broken", "stage": "searching the vault"},
                user_message=(
                    "I couldn't search your vault for this question. "
                    "I'll answer from what I know, but it may not be "
                    "grounded in your notes."),
                remedy_hint="Try restarting VaultBot.")
            results = []
        session_logger.log("vault_search", {
            "query": user_message,
            "k": 5,
            "result_count": len(results),
            "duration_ms": (loop.time() - t0) * 1000,
            "retriever": "fused",
            "rewritten_query": _rewritten_query[:200] if _rewritten_query != user_message else "",
        })

        # Conversation-aware retrieval: search the conversation index for
        # prior turns relevant to this query. This is what lets the bot
        # "remember what it just said" — when the user asks a follow-up,
        # the relevant prior turns are retrieved and injected into context
        # alongside the vault notes. Best-effort: never breaks the chat loop.
        _conv_results: list[dict] = []
        try:
            _conv_idx = getattr(svc, "conversation_index", None)
            if _conv_idx is not None and _conv_idx.size > 0:
                _conv_results = await loop.run_in_executor(
                    None, _conv_idx.search, _rewritten_query, 3)
                if _conv_results:
                    session_logger.log("conversation_search", {
                        "query": _rewritten_query[:100],
                        "result_count": len(_conv_results),
                        "top_score": _conv_results[0].get("score", 0) if _conv_results else 0,
                    })
        except Exception as e:  # noqa: BLE001
            session_logger.log("conversation_search_failed", {"error": str(e)})

        # RAG evaluation: log retrieval results for every query.
        try:
            svc.rag_evaluator.log_retrieval(user_message, results, k=5)
        except Exception as e:  # noqa: BLE001
            session_logger.log("rag_eval_log_failed", {"error": str(e)})

        # Lazy-condenser touch tracking: record that each retrieved note was
        # queried so the condenser can de-fluff it later.
        retrieved_paths = []
        try:
            for r in results:
                fp = r.get("file_path") if isinstance(r, dict) else None
                if fp:
                    retrieved_paths.append(fp)
                    svc.lazy_condenser.note_touched(fp)
            svc.lazy_condenser.flush_touch_counts()
        except Exception as e:  # noqa: BLE001
            session_logger.log("lazy_condenser_touch_failed", {"error": str(e)})

        # Procedure context tracking: which procedural notes were in the vault
        # context for this turn? Used to log validation results against them.
        procedures_in_context = parse_procedures_from_results(results)
        if procedures_in_context:
            session_logger.log("procedures_in_context", {
                "procedures": procedures_in_context,
            })

        # Multi-resolution context: L2 MOC + L1 concept cards + L0 drill-down.
        abs_ctx = await run_with_heartbeat(
            svc, websocket, "building context",
            build_abstract_context, svc.vault_graph, results,
            user_message, 5, 2, None)
        context = abs_ctx.get("context", "")
        session_logger.log("context_resolution", {
            "resolution": abs_ctx.get("resolution"),
            "l1_cards": abs_ctx.get("l1_cards", 0),
            "drill_down_used": abs_ctx.get("drill_down_used", False),
            "l0_drill": abs_ctx.get("l0_drill"),
            "context_length": len(context)})

        # Context budgeting: ensure the retrieved context fits within the
        # model's token budget.
        try:
            _budgeted = svc.context_budgeter.budget(
                context, getattr(websocket, "conversation_history", []))
            context = _budgeted["context"]
            if _budgeted["truncated"]:
                session_logger.log("context_budget", {
                    "original_tokens": _budgeted["original_tokens"],
                    "budgeted_tokens": _budgeted["budgeted_tokens"],
                    "budget": _budgeted["budget"],
                    "chars_dropped": _budgeted["chars_dropped"],
                })
        except Exception as e:  # noqa: BLE001
            session_logger.log("context_budget_failed", {"error": str(e)})

        # Phase 4: deterministic context filtering — drop irrelevant L1 card
        # sections so the big model sees only what's relevant to this query.
        # No longer gated on svc.small_client — the filter uses keyword
        # overlap, not an LLM call. Fail-safe: on any error, the full
        # context passes through unchanged.
        if len(context) > 3000:
            try:
                context = await filter_context(
                    svc, user_message, context, session_logger)
            except Exception as e:  # noqa: BLE001
                session_logger.log("context_filter_failed", {"error": str(e)})

        # Inject the identity boot context so the agent wakes up coherent.
        identity_context = svc.identity.boot_context()

        # Gather live state so the system prompt is a real briefing, not static.
        autonomous_state = svc.autonomous_researcher.status()
        try:
            _t_gaps = loop.time()
            gaps = await run_with_heartbeat(
                svc, websocket, "finding gaps",
                svc.knowledge_curriculum.propose_next_gaps, 10)
            session_logger.log("gaps_proposed", {
                "gap_count": len(gaps),
                "duration_ms": (loop.time() - _t_gaps) * 1000,
            })
        except Exception as e:  # noqa: BLE001
            session_logger.log("gaps_propose_failed", {"error": str(e)})
            await notify_info(svc, websocket,
                "I couldn't scan for knowledge gaps right now. "
                "This doesn't affect your answer.")
            gaps = []
        gaps_summary = "\n".join(
            f"- [{g.get('kind')}] {g.get('topic')} (priority {g.get('priority', 0)})"
            for g in gaps[:10]) or "(none detected)"

        # Build the combined tool list.
        custom_schemas = svc.self_improver.custom_tool_schemas()
        custom_tool_names = [s["function"]["name"] for s in custom_schemas]
        all_tools = build_tool_list(user_message, wm.render_for_prompt() if wm else "", custom_schemas)
        custom_tools_desc = "\n".join(
            f"- {s['function']['name']}: {s['function']['description'][:100]}"
            for s in custom_schemas) if custom_schemas else "(none yet)"

        # Build the DYNAMIC per-turn system prompt WITHOUT the vault context.
        # The briefing is rebuilt fresh every turn so newly-created tools and
        # edits appear immediately.
        _briefing = build_system_prompt_briefing(
            autonomous_state, gaps_summary,
            custom_tools=custom_tools_desc,
            custom_tool_names=custom_tool_names)
        # Phase 5: compress the stable parts (identity + briefing) via the
        # small model once per session and cache. This saves ~4-6K tokens
        # re-billed to the cloud every round. The dynamic parts (wm block,
        # procedure surface, conversation recall) are added uncompressed below.
        _stable_prompt = _compress_stable_prompt(
            identity_context, _briefing, session_logger)
        system_prompt = _stable_prompt
        # Inject the working-memory task list so the model sees its active plan
        # every round. render_for_prompt returns "" when there's no active plan
        # (simple Q&A is unaffected). The MODEL owns this list — the framework
        # just displays it.
        wm_block = wm.render_for_prompt()
        if wm_block:
            system_prompt = system_prompt + "\n\n" + wm_block

        # Procedure Discovery Service: surface one-line capability lines for
        # any procedures that FUSED retrieval matched for THIS query.
        try:
            _proc_idx = getattr(svc.procedure_tracker, "_stem_index", None)
            _proc_surface = build_procedure_surface(results, _proc_idx)
            if _proc_surface:
                system_prompt = system_prompt + "\n\n" + _proc_surface
                session_logger.log("procedure_surface", {
                    "lines": _proc_surface.count("\n"),
                })
                # --- Deterministic procedure routing hint ----------------
                # The small-model hint used to make a round-trip to Ollama to
                # pick which procedure matches the query. But FUSED retrieval
                # already ranked these same procedures by embedding+graph
                # similarity to the query — the best-matching one is simply
                # the highest-scored surfaced procedure. Reusing that score
                # is zero-LLM, zero-new-embedding, and never worse than the
                # small model's pick (it was choosing from the same surface).
                # Skipped for greetings/trivial messages (no procedure is the
                # right answer there) and for flagged procedures (can't run).
                try:
                    _hint = _deterministic_procedure_hint(
                        results, _proc_idx, user_message)
                    if _hint:
                        system_prompt += (
                            f"\n\n# PROCEDURE HINT (pre-classification — "
                            f"verify before executing): consider execute_procedure(\"{_hint}\") "
                            f"if it matches the task.")
                        session_logger.log("procedure_hint", {
                            "hint": _hint, "source": "fused_score",
                        })
                except Exception as e:  # noqa: BLE001
                    session_logger.log("procedure_hint_failed", {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            session_logger.log("procedure_surface_failed", {"error": str(e)})

        # If we're resuming an interrupted turn, tell the model what it already
        # did so it continues instead of re-running tools.
        if _resumed_tool_history:
            _lines = ["# RESUMED TURN (you were interrupted mid-task and are "
                      "continuing — do NOT re-run these tools, build on them):"]
            for _h in _resumed_tool_history[-15:]:
                if isinstance(_h, dict):
                    _lines.append(
                        f"- round {_h.get('round', '?')}: {_h.get('tool', '?')}"
                        f" → {_h.get('result_summary', '')[:120]}")
            system_prompt = system_prompt + "\n\n" + "\n".join(_lines)

        # Inject conversation recall: prior turns relevant to this query.
        # This is what lets the bot "remember what it just said" — the
        # conversation index retrieved turns that match the user's question,
        # and we inject them here so the model sees its own recent history
        # alongside the vault context. Skipped when there are no results.
        _conv_ctx_str = ""
        if _conv_results:
            try:
                _conv_ctx_str = build_conversation_context(_conv_results)
                if _conv_ctx_str:
                    system_prompt = system_prompt + "\n\n" + _conv_ctx_str
            except Exception as e:  # noqa: BLE001
                session_logger.log("conversation_context_inject_failed",
                    {"error": str(e)})

        session_logger.log("prompt_built", {
            "system_prompt_length": len(system_prompt),
            "vault_context_length": len(context),
            "context_length": len(context),
            "gaps_reported": len(gaps),
            "custom_tools": len(custom_schemas),
            "total_tools": len(all_tools),
            "conversation_turns_recalled": len(_conv_results),
        })

        # Build the conversation for /api/chat using PERSISTENT per-session history.
        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": (
                "# VAULT CONTEXT (retrieved for this query; compactable)\n"
                + context
            )},
        ]
        conversation.extend(getattr(websocket, "conversation_history", []))
        conversation.append({"role": "user", "content": user_message})

        # Sliding window: bound the conversation sent to the LLM.
        _pre_window_len = len(conversation)
        _ctx_win = 0
        try:
            _ctx_win = svc.ollama_client.context_window(
                svc.ollama_client.llm_model)
        except Exception as e:  # noqa: BLE001 — probe failure is non-fatal; log + use 0 so the sliding window applies its default guard
            session_logger.log("context_window_probe_failed", {
                "where": "pre_loop", "error": str(e)})
        conversation = _apply_sliding_window(
            conversation, context_window_tokens=_ctx_win)
        if len(conversation) < _pre_window_len:
            session_logger.log("sliding_window_applied", {
                "messages_before": _pre_window_len,
                "messages_after": len(conversation),
            })

        # History budget: cap the history tokens sent to the big model per
        # round. The sliding window's 60% guard effectively never truncates on
        # cloud models with large context windows — this tight second pass
        # ensures we don't re-bill a growing history pile every round.
        _pre_budget_len = len(conversation)
        conversation = _enforce_history_budget(
            conversation, session_logger=session_logger)
        if len(conversation) < _pre_budget_len:
            session_logger.log("history_budget_applied", {
                "messages_before": _pre_budget_len,
                "messages_after": len(conversation),
            })

        # Token-usage meter: report how full the context window is.
        try:
            _total_chars = sum(len(str(m.get("content", "") or ""))
                               for m in conversation if isinstance(m, dict))
            _used_tokens = max(1, _total_chars // 4)
            _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
            await svc.manager.send_personal_message(json.dumps({
                "type": "context_usage",
                "model": svc.ollama_client.llm_model,
                "context_window": _ctx_window,
                "used_tokens": _used_tokens,
                "available_tokens": max(0, _ctx_window - _used_tokens),
                "messages": len(conversation),
            }), websocket, session_logger=session_logger)
        except Exception as _e:  # noqa: BLE001
            session_logger.log("context_usage_emit_failed", {"error": str(_e)})

        await svc.manager.send_personal_message(json.dumps({"type": "status", "content": "Thinking..."}), websocket, session_logger=session_logger)

        # --- Trivial-turn shortcut (Phase 7: skip big model) ---------------
        # Simple greetings, thanks, and meta-questions are routed to the
        # small model directly, saving cloud tokens. Conservative: falls
        # through to the big-model agentic loop on any failure.
        if _classify_trivial(user_message, getattr(websocket, "conversation_history", []), wm):
            try:
                from llm_client import get_small_client
                _trivial_client = get_small_client(session_logger)
                if _trivial_client is not None:
                    _trivial_identity = svc.identity.boot_context()
                    _trivial_prompt = (
                        f"{_trivial_identity}\n\n"
                        f"The user said: \"{user_message}\"\n"
                        f"Answer briefly and naturally. Be warm and concise. "
                        f"Do not call any tools.")
                    _trivial_resp = _trivial_client.chat(
                        [{"role": "user", "content": _trivial_prompt}],
                        temperature=0.5, stream=False, think=False,
                        max_predict=512)
                    _trivial_text = ""
                    if isinstance(_trivial_resp, dict):
                        _msg = _trivial_resp.get("message", {})
                        if isinstance(_msg, dict):
                            _trivial_text = _msg.get("content", "") or ""
                        if not _trivial_text:
                            _trivial_text = _trivial_resp.get("response", "") \
                                or _trivial_resp.get("content", "")
                    _trivial_text = (_trivial_text or "").strip()
                    # Only accept the shortcut if the small model produced a
                    # real response. Otherwise fall through to the big model.
                    if len(_trivial_text) >= 10:
                        session_logger.log("trivial_turn_shortcut", {
                            "user_message": user_message[:80],
                            "response_chars": len(_trivial_text),
                        })
                        await svc.manager.send_personal_message(
                            json.dumps({"type": "answer_chunk",
                                        "content": _trivial_text}),
                            websocket, session_logger=session_logger)
                        await svc.manager.send_personal_message(
                            json.dumps({"type": "answer_done",
                                        "content": _trivial_text}),
                            websocket, session_logger=session_logger)
                        # Save to conversation history.
                        _new_turns = [
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": _trivial_text},
                        ]
                        websocket.conversation_history = (
                            getattr(websocket, "conversation_history", [])
                            + _new_turns)
                        save_history(websocket.conversation_history)
                        session_logger.log("chat_end", {
                            "answer_length": len(_trivial_text),
                            "thinking_length": 0,
                            "tool_rounds": 0,
                        })
                        return  # skip the entire agentic loop
                    # Fall through — small model gave nothing useful.
                    session_logger.log("trivial_turn_fallback_empty", {
                        "user_message": user_message[:80],
                    })
            except Exception as e:  # noqa: BLE001 — best-effort: fall through to big model
                session_logger.log("trivial_turn_fallback_error", {
                    "error": str(e),
                    "user_message": user_message[:80],
                })

        # --- Agentic loop: model speaks → tool calls (if any) → repeat → final ---
        # The model decides to call tools, when, and when to stop. The framework
        # NEVER blocks, rejects, or auto-marks anything.
        final_answer = ""
        thinking_text = ""
        total_chunks = 0
        t0 = loop.time()
        _turn_tool_history: list = list(_resumed_tool_history)
        _tool_rounds_executed = 0
        _double_silent_once = False
        # Guard against dangling "let me show you..." endings (see session
        # 9450d6ad). If the model says it will demonstrate then cuts out,
        # loop once more so it actually delivers. Bounded to 1 retry.
        _dangling_retries = 0
        # Detect a thinking/reasoning model by name so we can pre-digest large
        # tool results for it (fix #1). A thinking model that gets a huge raw
        # file dump tends to ECHO it char-by-char in its reasoning field and
        # exhaust its budget before writing the answer. For those models we
        # inject a compact structural digest instead of the raw blob.
        _is_thinking_model = any(k in (svc.ollama_client.llm_model or "").lower()
                                 for k in ("thinking", "reason", "nemotron", "o1",
                                           "qwq", "r1", "o3", "deepseek-reason"))

        # Per-step RAG tracking: retrieve notes for the current in-progress step.
        # The model calls update_task to mark progress; we track which step is
        # in_progress by inspecting the working memory's state each round.
        _last_step_rag_key: str = ""
        # Step context block — overwritten each round, NOT accumulated.
        # Previously this was appended to conversation[0] every round, causing
        # (a) N×growth in the system prompt and (b) immediate overwrite by the
        # wm-block refresh below (the step context was lost). Now we track it
        # separately and compose it into conversation[0] in one shot.
        _step_ctx_block: str = ""
        # Token cost tracking: accumulate per-round ollama_stats token counts
        # so we can log and emit a cumulative total per turn. This is the lever
        # for measuring cost-reduction changes — without it, we're tuning blind.
        _turn_token_totals = {"prompt_tokens": 0, "completion_tokens": 0, "rounds": 0}
        # Seen-content tracker: per-turn set of {file_path: {"source": str,
        # "lines": (start, end)|None, "round": int}}. Populated by vault_search
        # and code_read. Used to dedup vault_search results so the model doesn't
        # re-search for files it already has, breaking the search loop.
        _seen_content: dict[str, dict[str, Any]] = {}
        # Seed with the initial FUSED retrieval results — those files are
        # already in the vault context (conversation[1]) so the model has
        # already "seen" them. This prevents the first vault_search from
        # returning the same files that are already in context.
        for _r in results:
            _fp = _r.get("file_path", "") if isinstance(_r, dict) else ""
            if _fp:
                _seen_content[_fp] = {
                    "source": "initial_context",
                    "lines": None,
                    "round": -1,
                }
        # --- Findings ledger (anti-amnesia) ---
        # A per-turn list of 1-line entries: "R{n}: {tool} → {summary}".
        # Injected into the system prompt every round as "# FINDINGS SO FAR".
        # This survives history budget truncation because it lives in the
        # system prompt, not the conversation history. The model always sees
        # what it already did without re-reading dropped messages. Zero LLM
        # cost (deterministic), never returns None (unlike compress_window).
        _findings: list[str] = []
        # Go-find-out escalation: counts consecutive vault_search calls
        # where ALL results were already seen. When this hits the threshold,
        # the harness auto-runs vault_research on the user's question to go
        # find the missing information on the web instead of looping.
        _consecutive_all_seen = 0
        _go_find_out_fired = False
        # When go-find-out fires, the research summary is stored here so it
        # can be injected as a system message after the tool results are
        # appended. A system message is more authoritative than a tool result
        # — the model treats it as framework-level instruction, not optional data.
        _go_find_out_msg: str = ""

        # Partial-answer crash protection: write the streamed-so-far answer to a
        # temp file so a crash mid-stream doesn't lose it.
        import hashlib
        import tempfile
        import time as _time
        partial_dir = Path(tempfile.gettempdir()) / "vaultbot_partials"
        partial_dir.mkdir(parents=True, exist_ok=True)
        partial_id = hashlib.blake2b((user_message + str(_time.time())).encode(), digest_size=12).hexdigest()[:12]
        partial_path = partial_dir / f"partial_{partial_id}.md"
        write_partial(partial_path, user_message, "", "")

        try:
         # --- Core Copilot-style loop: the model drives, the harness supports ---
         # No framework_plan. No phase state machine. No auto-advance. No forced
         # convergence. The model calls plan_task / update_task if it wants to
         # stay on track; the harness re-injects the wm block every round.
         # The loop ends when the model produces a turn with no tool calls.
         round_idx = 0
         # Long-horizon default: the loop runs until the model produces a
         # turn with no tool calls (a natural stop). There is NO meaningful
         # round cap for normal work — Copilot's harness has no such cap and
         # the same model runs for hours there. VAULTBOT_MAX_ROUNDS is a
         # SAFETY NET against a truly runaway loop (infinite tool calls with
         # no progress), set very high so it never trips on real work. The
         # model, not the framework, decides when a long task is done.
         _MAX_ROUNDS = int(os.getenv("VAULTBOT_MAX_ROUNDS", "2000"))
         # Convergence nudge fires near the safety net, not at a fixed 40.
         # Default: 90% of MAX_ROUNDS. On a 2000-round net that's round 1800,
         # which a real task will never reach. Override with the env var if
         # you want it earlier.
         _CONVERGENCE_NUDGE_ROUND = int(os.getenv(
             "VAULTBOT_CONVERGENCE_NUDGE",
             str(max(10, int(_MAX_ROUNDS * 0.9)))))
         # --- Read-loop detector (prevents diagnostic loops) ---
         # Tracks read-only vs write tool calls per turn.  If the model
         # calls many read tools without ever writing, inject escalating
         # nudges to force action.  This is the same pattern production
         # agents (Copilot, Claude Code, Cline) use to prevent the model
         # from reading 20 files and never making an edit.
         _READ_TOOLS = frozenset({
             "code_read", "vault_search", "vault_read_note", "vault_list", "thought",
             "web_read_source", "vault_graph_analyzer",
             "vault_cluster_analyzer", "vault_lint",
         })
         _WRITE_TOOLS = frozenset({
             "safe_write", "code_run", "vault_safe_write", "vault_append",
             "vault_delete", "tool_create", "js_safe_write",
             "execute_procedure", "vault_research",
         })
         _turn_read_count = 0
         _turn_write_count = 0
         _turn_failed_write_count = 0  # write tools that didn't actually write
         _turn_effective_read_count = 0  # reads − successful writes (net reads)
         _read_loop_warned_soft = False   # 8 effective reads, 0 successful writes
         _read_loop_warned_hard = False   # 12 effective reads
         _read_loop_forced = False        # 15 effective reads → force stop
         # --- Plan enforcement (one rule, not a heuristic stack) ---
         # If the model fires tools for N rounds without creating a plan,
         # the framework injects "call plan_task NOW" and masks execution
         # tools until a plan exists. This is NOT a signal-word heuristic —
         # it's a simple round counter. The model can still call read-only
         # tools (explore) while the gate is active. The gate lifts the
         # moment the model calls plan_task.
         _FORCE_PLAN_ROUNDS = int(os.getenv("VAULTBOT_FORCE_PLAN_ROUNDS", "3"))
         _rounds_without_plan = 0
         _plan_gate_active = False  # True = execution tools masked
         while round_idx < _MAX_ROUNDS:
            # --- Read-loop hard enforcement (gated on NO plan) ---
            # This break exists to stop GENUINE THRASH: the model is calling
            # read tools with no plan, no task list, and no successful
            # writes — i.e. it has no idea what it's doing and is spinning.
            #
            # It must NOT fire on a legitimate long-horizon task. A planned
            # research/architecture step can legitimately read 30+ files
            # before its first write; that is the model WORKING, not stuck.
            # Copilot's harness lets the same model read indefinitely. So we
            # only force-break when there is no plan at all (the model never
            # called plan_task AND the framework hasn't auto-planned) — that
            # is the real signal of an unstructured thrash loop.
            #
            # Two break triggers, both require no plan:
            #   1. 18+ effective reads with no plan — unstructured read spin.
            #   2. 3+ consecutive failed writes — the model keeps calling a
            #      broken write tool without fixing the root cause. This one
            #      fires regardless of plan, because a plan can't save a
            #      model that's hammering a broken tool.
            _break_reason = None
            if (not wm.has_plan()
                    and _read_loop_forced
                    and _turn_effective_read_count >= 18):
                _break_reason = "read_streak_unplanned"
            elif _turn_failed_write_count >= 3:
                _break_reason = "failed_write_streak"
            if _break_reason:
                session_logger.log("read_loop_break", {
                    "round": round_idx,
                    "break_reason": _break_reason,
                    "read_count": _turn_read_count,
                    "write_count": _turn_write_count,
                    "failed_write_count": _turn_failed_write_count,
                    "effective_read_count": _turn_effective_read_count,
                })
                session_logger.log("loop_exit", {
                    "reason": "read_loop_break",
                    "break_reason": _break_reason,
                    "round": round_idx,
                    "total_tools": _tool_rounds_executed,
                    "total_text_chars": len(final_answer),
                    "findings_count": len(_findings),
                    "effective_read_count": _turn_effective_read_count,
                    "failed_write_count": _turn_failed_write_count,
                })
                final_answer += (
                    "\n\n*The diagnostic loop was stopped by the framework "
                    f"after {round_idx + 1} rounds ({_break_reason}: "
                    f"{_turn_effective_read_count} effective reads, "
                    f"{_turn_failed_write_count} failed writes). "
                    "The findings above are what I was able to determine, "
                    "but I was unable to complete the task within this turn.*"
                )
                break

            # --- Plan enforcement: force planning after N rounds without a plan ---
            # ONE rule, not a heuristic stack: if the model has fired tools for
            # _FORCE_PLAN_ROUNDS rounds without calling plan_task, inject a
            # directive and mask execution tools. The model can still call
            # read-only tools (explore) while the gate is active. The gate
            # lifts when the model calls plan_task. This catches the "model
            # drives blind, no structured task list, no convergence target"
            # failure that caused the 60-round loop.
            if not wm.has_plan() and _tool_rounds_executed > 0:
                _rounds_without_plan += 1
                if _rounds_without_plan == _FORCE_PLAN_ROUNDS and not _plan_gate_active:
                    _plan_gate_active = True
                    conversation.append({
                        "role": "system",
                        "content": (
                            f"# PLAN REQUIRED: You've worked for {_rounds_without_plan} "
                            f"tool rounds without a plan. Call plan_task NOW "
                            f"with a goal and concrete ordered steps. Execution "
                            f"tools (safe_write, execute_procedure, vault_research, "
                            f"tool_create, etc.) are masked until you do. You can "
                            f"still use read-only tools (code_read, vault_search, "
                            f"vault_list) to explore. Once you call plan_task, the "
                            f"restriction lifts and you proceed step by step."
                        ),
                    })
                    session_logger.log("force_plan_triggered", {
                        "round": round_idx,
                        "rounds_without_plan": _rounds_without_plan,
                    })
            elif wm.has_plan():
                if _plan_gate_active:
                    _plan_gate_active = False
                    session_logger.log("plan_gate_lifted", {
                        "round": round_idx,
                    })
                _rounds_without_plan = 0

            # Build the tool list for this round. When the plan gate is
            # active, mask to plan_task + read-only tools only.
            if _plan_gate_active:
                _round_tools = [t for t in all_tools
                                if t.get("function", {}).get("name", "") in
                                (_READ_TOOLS | {"plan_task", "thought"})]
                session_logger.log("plan_gate_active", {
                    "round": round_idx,
                    "masked_tools": len(_round_tools),
                    "total_tools": len(all_tools),
                })
            else:
                _round_tools = all_tools

            session_logger.log("round_loop_top", {
                "round": round_idx, "t_ms": loop.time() * 1000,
                "conv_msgs": len(conversation),
            })

            # --- Per-step RAG retrieval (surfaces relevant procedures) ---
            # Only fires when the model has an in-progress task. Retrieves notes
            # relevant to the current step's content so the model sees procedures
            # it might need. Appended to conversation[0] before the model speaks.
            _wm_snap = wm.snapshot() if wm.has_plan() else {}
            _active_task = None
            for _t in _wm_snap.get("tasks", []):
                if _t.get("status") == "in_progress":
                    _active_task = _t
                    break
            if _active_task and _active_task.get("content"):
                _step_key = _active_task.get("id", "") + ":" + _active_task["content"][:100]
                if _step_key != _last_step_rag_key:
                    try:
                        _rag_query = _small_model_query(
                            _wm_snap.get("goal", ""),
                            _active_task["content"],
                            session_logger)
                        _step_results = await run_with_heartbeat(
                            svc, websocket, "retrieving context for step",
                            svc.fused_retriever.retrieve, _rag_query, 3, 0)
                        _step_notes = _step_results.get("results", []) if isinstance(_step_results, dict) else (_step_results or [])
                        if _step_notes:
                            _step_ctx_parts = ["# STEP CONTEXT (retrieved for your current task step — these notes are relevant to what you're doing now)"]
                            for _r in _step_notes[:3]:
                                _fp = _r.get("file_path", "")
                                _stem = Path(_fp).stem if _fp else "?"
                                _snippet = (_r.get("content") or _r.get("snippet") or "")[:500]
                                _step_ctx_parts.append(f"## [[{_stem}]]\n{_snippet}")
                            # Store in _step_ctx_block — composed into
                            # conversation[0] by the wm-block refresh below.
                            # This replaces (not appends) the previous block so
                            # the system prompt stays constant size across
                            # rounds instead of growing N×.
                            _step_ctx_block = "\n\n".join(_step_ctx_parts)
                            session_logger.log("step_rag_retrieved", {
                                "round": round_idx,
                                "task_id": _active_task.get("id"),
                                "task_content": _active_task["content"][:80],
                                "notes_found": len(_step_notes),
                            })
                    except Exception as e:  # noqa: BLE001
                        session_logger.log("step_rag_failed", {"error": str(e)})
                    _last_step_rag_key = _step_key

            # --- Refresh the wm block + step context in the system prompt ---
            # Compose conversation[0] in ONE shot from the stable base + live wm
            # block + live step context. This replaces the old two-stage approach
            # where step context was appended to conversation[0] and then
            # immediately overwritten by the wm refresh (losing the step context
            # and causing wasted retrieval). Now all three components are
            # composed together so the system prompt stays constant size.
            try:
                _wm_block = wm.render_for_prompt()
                _base = system_prompt
                _composed = _base
                if _wm_block:
                    _composed += "\n\n" + _wm_block
                if _step_ctx_block:
                    _composed += "\n\n" + _step_ctx_block
                # Inject the findings ledger (capped at 15 entries, oldest trimmed).
                if _findings:
                    _findings_block = "# FINDINGS SO FAR (this turn — your progress ledger)\n" + "\n".join(_findings[-15:])
                    _composed += "\n\n" + _findings_block
                conversation[0] = {"role": "system", "content": _composed}
            except Exception as e:  # noqa: BLE001 — wm render is best-effort; the base system prompt passes through on failure
                session_logger.log("wm_render_failed", {"error": str(e)})
                conversation[0] = {"role": "system", "content": system_prompt}

            # Stream the LLM response for this round.
            round_text = ""
            round_thinking = ""
            round_tool_calls = []
            round_finish_reason: str | None = None
            chunk_count = 0
            session_logger.log("llm_stream_start", {
                "round": round_idx, "conv_msgs": len(conversation),
                "conv_chars": sum(len(str(m.get("content","") or "")) for m in conversation),
                "t_ms": loop.time() * 1000,
            })
            # SANITIZE conversation: convert tool-call rounds to model-safe format.
            _model_conversation = _sanitize_tool_history(conversation)

            try:
                def sync_stream():
                    session_logger.log("ollama_chat_call_enter", {
                        "round": round_idx, "t_ms": time.time() * 1000,
                    })
                    for chunk in svc.ollama_client.chat(_model_conversation, tools=_round_tools, stream=True):
                        yield chunk
                    session_logger.log("ollama_chat_call_exit", {
                        "round": round_idx, "t_ms": time.time() * 1000,
                    })
                gen = sync_stream()
                round_t0 = loop.time()
                last_chunk_at = loop.time()
                while True:
                    next_chunk_task = loop.run_in_executor(None, lambda: next(gen, {"done": True}))
                    chunk = None
                    while chunk is None:
                        try:
                            chunk = await asyncio.wait_for(
                                asyncio.shield(next_chunk_task), timeout=3.0)
                        except TimeoutError:
                            elapsed = int((loop.time() - round_t0) * 1000)
                            since = int((loop.time() - last_chunk_at) * 1000)
                            await svc.manager.send_personal_message(json.dumps({
                                "type": "heartbeat", "label": f"thinking (round {round_idx+1})",
                                "elapsed_ms": elapsed, "silent_ms": since,
                                "chunks": chunk_count,
                            }), websocket, session_logger=session_logger)
                        except asyncio.CancelledError:
                            gen.close()
                            raise
                    if chunk.get("done") and not chunk.get("response") and not chunk.get("tool_calls"):
                        if chunk.get("finish_reason"):
                            round_finish_reason = chunk["finish_reason"]
                        break
                    if chunk.get("eval_stats"):
                        _es = chunk["eval_stats"]
                        _prompt_tps = 0.0
                        _gen_tps = 0.0
                        if _es.get("prompt_eval_duration", 0) > 0:
                            _prompt_tps = _es["prompt_eval_count"] / (_es["prompt_eval_duration"] / 1e9)
                        if _es.get("eval_duration", 0) > 0:
                            _gen_tps = _es["eval_count"] / (_es["eval_duration"] / 1e9)
                        await svc.manager.send_personal_message(json.dumps({
                            "type": "ollama_stats",
                            "load_duration_ms": _es.get("load_duration", 0) / 1e6,
                            "prompt_eval_count": _es.get("prompt_eval_count", 0),
                            "prompt_eval_duration_ms": _es.get("prompt_eval_duration", 0) / 1e6,
                            "prompt_tokens_per_s": round(_prompt_tps, 1),
                            "eval_count": _es.get("eval_count", 0),
                            "eval_duration_ms": _es.get("eval_duration", 0) / 1e6,
                            "gen_tokens_per_s": round(_gen_tps, 1),
                            "total_duration_ms": _es.get("total_duration", 0) / 1e6,
                        }), websocket, session_logger=session_logger)
                        # Accumulate token counts for per-turn cost tracking.
                        _turn_token_totals["prompt_tokens"] += _es.get("prompt_eval_count", 0)
                        _turn_token_totals["completion_tokens"] += _es.get("eval_count", 0)
                        continue
                    chunk_count += 1
                    total_chunks += 1
                    last_chunk_at = loop.time()
                    thinking = chunk.get("thinking", "")
                    text = chunk.get("response", "")
                    tcs = chunk.get("tool_calls", [])
                    if thinking:
                        round_thinking += thinking
                        thinking_text += thinking
                        await svc.manager.send_personal_message(json.dumps({"type": "thinking", "content": thinking}), websocket, session_logger=session_logger)
                    if text:
                        round_text += text
                        await svc.manager.send_personal_message(json.dumps({"type": "answer_chunk", "content": text}), websocket, session_logger=session_logger)
                        write_partial(partial_path, user_message, final_answer + round_text, thinking_text)
                    if tcs:
                        round_tool_calls.extend(tcs)
            except Exception as e:
                session_logger.log_exception(e, context="ollama_client.chat")
                if round_text:
                    write_partial(partial_path, user_message,
                                  final_answer + round_text, thinking_text)
                from diagnostics import classify_error
                diag = classify_error(e, {"stage": "thinking"})
                await svc.manager.send_personal_message(
                    json.dumps({"type": "problem", "diagnosis": diag.to_dict()}),
                    websocket, session_logger=session_logger)
                raise

            session_logger.log("agent_round", {
                "round": round_idx,
                "chunk_count": chunk_count,
                "text_length": len(round_text),
                "tool_calls": len(round_tool_calls),
            })

            # Append the assistant's turn to the conversation.
            assistant_msg = {"role": "assistant", "content": round_text}
            if round_thinking:
                assistant_msg["thinking"] = round_thinking
            if round_tool_calls:
                assistant_msg["tool_calls"] = round_tool_calls
            conversation.append(assistant_msg)

            # ───────────────────────────────────────────────────────────────
            # COPILOT-STYLE: the model drives. No phases, no rejections.
            # ───────────────────────────────────────────────────────────────
            # Model produced text (no tool calls). Before accepting as the
            # final answer, guard against a DANGLING continuation — a thinking
            # model that exhausted its reasoning budget mid-echo and emitted a
            # content-free promise ("let me show you...", "I'll demonstrate:")
            # instead of a real answer. Accepting that as final leaves the user
            # with a sentence that trails off and never delivers. We detect the
            # dangling pattern and loop ONE more time so the model actually
            # delivers instead of cutting out. Bounded by _dangling_retries so
            # it can't loop forever.
            if not round_tool_calls:
                _looks_dangling = False
                _t = (round_text or "").strip()
                if round_text.strip():
                    # Ends with a colon/ellipsis/"let me ..." promise = the model
                    # said it would demonstrate something but then didn't. That
                    # is the "cut out" symptom (see session 9450d6ad: thinking
                    # echo exhausted the budget, answer was a dangling intro).
                    import re as _re
                    _lower = _t.lower()
                    if (_t.endswith(":") or _t.endswith("...") or _t.endswith("—")
                            or _re.search(r"(let me|let's|let us|i'll|i will|i'm going to|allow me to|here's|here is)\b[^.?!]*$", _lower)
                            or _re.search(r"(demonstrate|show you|dive into|walk you through|break down|explore|cover|go through|look at|start with|begin|first|next|so|now|well|basically|essentially|in short|to summarize|to recap)\s*[:.]?\s*$", _lower)):
                        _looks_dangling = True
                if _looks_dangling and _dangling_retries < 1:
                    _dangling_retries += 1
                    session_logger.log("dangling_answer_continuation", {
                        "round": round_idx,
                        "retry": _dangling_retries,
                        "answer_preview": _t[:80]})
                    # Tell the model to FINISH, not re-promise. This is a
                    # continuation, not a rejection — it builds on what it has.
                    conversation.append({
                        "role": "user",
                        "content": (
                            "You just said you would show/demonstrate something "
                            "but ended there. Deliver it now — write out the "
                            "actual content you promised (using the tool results "
                            "already in context), do NOT just say you will. Do "
                            "not call more tools unless you truly need new data."),
                    })
                    round_idx += 1
                    continue

                if round_text.strip() or round_thinking.strip():
                    # PLAN-AWARE CONTINUATION: if the model wrote text without
                    # a tool call but there's an active plan with unfinished
                    # tasks, nudge it to continue instead of accepting prose
                    # as the final answer. This prevents premature termination
                    # when the model emits partial synthesis while tasks remain.
                    if wm.has_plan() and not wm.all_done():
                        _unfinished = [t for t in wm.tasks if t.status != "completed"]
                        session_logger.log("plan_continuation_nudge", {
                            "round": round_idx,
                            "unfinished_count": len(_unfinished),
                            "unfinished_titles": [t.content[:80] for t in _unfinished[:5]],
                        })
                        conversation.append({
                            "role": "user",
                            "content": (
                                f"You have {len(_unfinished)} unfinished task(s) in your plan. "
                                f"Continue working — call the tool you need for the next step, "
                                f"or call update_task to mark progress. "
                                f"If you are truly done, mark all tasks completed first."
                            ),
                        })
                        round_idx += 1
                        continue

                    final_answer += round_text
                    session_logger.log("turn_done", {
                        "round": round_idx,
                        "answer_length": len(final_answer),
                        "tool_rounds": _tool_rounds_executed,
                        "finish_reason": round_finish_reason or "stop",
                    })
                    session_logger.log("loop_exit", {
                        "reason": "natural_done",
                        "round": round_idx,
                        "total_tools": _tool_rounds_executed,
                        "total_text_chars": len(final_answer),
                        "findings_count": len(_findings),
                        "plan_had_tasks": wm.has_plan(),
                    })
                    break

                # Double-silent failsafe (the ONLY framework-level guard).
                if not round_text.strip() and not round_thinking.strip():
                    if not _double_silent_once:
                        _double_silent_once = True
                        session_logger.log("silent_turn_retry", {
                            "round": round_idx})
                        conversation.append({
                            "role": "user",
                            "content": "(no response received — please reply)",
                        })
                        round_idx += 1
                        continue
                    # Second silent turn → fail loud.
                    session_logger.log("agent_silent_fail_loud", {
                        "round": round_idx,
                        "tool_rounds": _tool_rounds_executed,
                    })
                    raise AgentSilentError(
                        "Model returned nothing on two consecutive turns. "
                        "Please retry.")

            # Model called tools → execute them and feed results back.
            _tool_rounds_executed += 1
            _double_silent_once = False
            _dangling_retries = 0

            # --- Read-loop tracking: pre-execution name-based read count ---
            # Count read tools by name (reads are always reads regardless of
            # result). Write success is determined AFTER execution by
            # _tool_actually_wrote() — a failed write does NOT reset the
            # read counter (the old bug that let the model loop forever).
            _round_has_write_tool = False
            for tc in round_tool_calls:
                _tn = (tc.get("function", {}) or {}).get("name", "")
                if _tn in _WRITE_TOOLS:
                    _round_has_write_tool = True
                elif _tn in _READ_TOOLS:
                    _turn_read_count += 1

            # Accumulate non-final round text so partial file captures all streamed text.
            if round_text.strip() and round_text.strip() != ".":
                final_answer += round_text

            # Execute each tool call and feed results back as tool-role messages.
            for tc in round_tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                tool_args_raw = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
                except json.JSONDecodeError:
                    tool_args = {}
                tool_call_id = tc.get("id", tool_name)

                await svc.manager.send_personal_message(json.dumps({
                    "type": "tool_call", "tool": tool_name, "args": tool_args
                }), websocket, session_logger=session_logger)
                session_logger.log("tool_call_requested", {
                    "tool": tool_name, "args": tool_args, "round": round_idx,
                })

                t_tool0 = loop.time()
                session_logger.log("tool_exec_enter", {
                    "tool": tool_name, "round": round_idx,
                    "t_ms": t_tool0 * 1000,
                })
                try:
                    tool_result = await execute_agent_tool(
                        svc, tool_name, tool_args, session_logger, websocket,
                        user_message=user_message)
                except Exception as e:  # noqa: BLE001
                    session_logger.log_exception(e, context=f"tool_{tool_name}")
                    tool_result = {"error": str(e)}

                # --- Seen-content tracking for vault_search & code_read ----
                # Track which files the model has seen this turn so we can
                # dedup future vault_search results and break search loops.
                if tool_name == "vault_search" and isinstance(tool_result, dict):
                    for _r in tool_result.get("results", []):
                        _fp = _r.get("file_path", "")
                        if _fp:
                            _seen_content[_fp] = {
                                "source": "vault_search",
                                "lines": None,
                                "round": round_idx,
                            }
                elif tool_name in ("code_read", "vault_read_note") \
                        and isinstance(tool_result, dict) \
                        and not tool_result.get("error"):
                    _fp = tool_result.get("file_path", "")
                    _sl = tool_result.get("start_line", 1)
                    _el = tool_result.get("end_line", 0)
                    _tl = tool_result.get("total_lines", 0)
                    if _fp:
                        # If the read covered the whole file, mark it fully seen.
                        # Otherwise track the specific line range.
                        _full = (_sl <= 1 and (_el <= 0 or _el >= _tl))
                        _seen_content[_fp] = {
                            "source": tool_name,
                            "lines": None if _full else (_sl, _el),
                            "round": round_idx,
                        }

                # --- Annotate vault_search results against seen content ------
                # If the model calls vault_search and gets back files it
                # already saw (via a previous vault_search or code_read),
                # annotate them with "already_in_context: true" so the model
                # can see that its searches are returning things it already
                # has. When ALL results are already seen, inject a strong
                # "stop searching" message. This breaks the "search anxiety"
                # loop where the model keeps rephrasing the same query.
                if tool_name == "vault_search" and isinstance(tool_result, dict):
                    _raw_results = tool_result.get("results", [])
                    _annotated, _already_seen = _dedup_seen_results(
                        _raw_results, _seen_content)
                    if _already_seen:
                        _seen_names = ", ".join(
                            Path(o["file_path"]).stem
                            for o in _already_seen[:10])
                        session_logger.log("search_results_deduped", {
                            "round": round_idx,
                            "raw_count": len(_raw_results),
                            "already_seen": len(_already_seen),
                            "new_results": len(_annotated) - len(_already_seen),
                            "seen_files": [Path(o["file_path"]).stem
                                           for o in _already_seen[:10]],
                        })
                        tool_result["results"] = _annotated
                        _new_count = len(_annotated) - len(_already_seen)
                        if _new_count == 0:
                            # ALL results were already seen — increment the
                            # go-find-out escalation counter.
                            _consecutive_all_seen += 1
                            _go_find_out_threshold = int(
                                os.getenv("VAULTBOT_GO_FIND_OUT_THRESHOLD", "3"))
                            if _consecutive_all_seen >= _go_find_out_threshold \
                                    and not _go_find_out_fired:
                                # GO FIND OUT: the vault doesn't have what the
                                # model needs. Auto-trigger web research on the
                                # user's original question and inject the result
                                # as a tool result so the model gets new info.
                                _go_find_out_fired = True
                                session_logger.log("go_find_out_triggered", {
                                    "round": round_idx,
                                    "consecutive_all_seen": _consecutive_all_seen,
                                    "query": user_message[:100],
                                })
                                await svc.manager.send_personal_message(
                                    json.dumps({"type": "status",
                                        "content": "Vault doesn't have enough — researching on the web..."}),
                                    websocket, session_logger=session_logger)
                                try:
                                    _research_result = await execute_agent_tool(
                                        svc, "vault_research",
                                        {"topic": user_message[:200],
                                         "depth": "quick"},
                                        session_logger, websocket,
                                        user_message=user_message)
                                    # Build a compact summary of the research
                                    # result for the system message.
                                    _research_brief = ""
                                    if isinstance(_research_result, dict):
                                        _rb = _research_result.get(
                                            "synthesis_brief", "")
                                        _kf = _research_result.get(
                                            "key_facts", "")
                                        _np = _research_result.get(
                                            "note_path", "")
                                        _parts = []
                                        if _rb:
                                            _parts.append(_rb[:2000])
                                        if _kf:
                                            _parts.append(f"Key facts:\n{_kf}")
                                        if _np:
                                            _parts.append(
                                                f"A permanent note was "
                                                f"created at {_np}.")
                                        _research_brief = "\n\n".join(_parts)
                                    # Store the system message for injection
                                    # after the tool results are appended.
                                    _go_find_out_msg = (
                                        f"# GO-FIND-OUT: Web research "
                                        f"completed automatically\n"
                                        f"The vault did not contain enough "
                                        f"information for this question after "
                                        f"{_consecutive_all_seen} searches. "
                                        f"I automatically researched it on the "
                                        f"web. Here are the results:\n\n"
                                        f"{_research_brief or '(no summary available)'}\n\n"
                                        f"Use these research results to answer "
                                        f"the user's question NOW. Do NOT call "
                                        f"vault_search again. Do NOT look for "
                                        f"procedures. You have the information "
                                        f"— write your answer.")
                                    # Also keep the tool result for the model
                                    # to see in the tool response.
                                    tool_result = {
                                        "go_find_out": True,
                                        "message": (
                                            "Web research completed "
                                            "automatically. See the system "
                                            "message for results. Use them "
                                            "to answer now — do NOT search "
                                            "again."),
                                        "research": _research_result,
                                    }
                                except Exception as e:  # noqa: BLE001
                                    session_logger.log("go_find_out_failed", {
                                        "error": str(e)})
                                    tool_result["message"] = (
                                        f"All search results are files you "
                                        f"already have, and auto-research "
                                        f"failed ({e}). Answer from what you "
                                        f"already have — do NOT search again.")
                            else:
                                # Below threshold or already fired — tell the
                                # model to stop searching and answer.
                                tool_result["message"] = (
                                    f"All {len(_already_seen)} search results "
                                    f"are files you ALREADY retrieved this turn: "
                                    f"{_seen_names}. You have all the information "
                                    f"the vault contains on this topic. "
                                    f"STOP SEARCHING. Write your answer now "
                                    f"using the notes you already have. "
                                    f"Do NOT call vault_search again.")
                        else:
                            # Some new results — reset the counter.
                            _consecutive_all_seen = 0
                session_logger.log("tool_exec_exit", {
                    "tool": tool_name, "round": round_idx,
                    "duration_ms": (loop.time() - t_tool0) * 1000,
                })
                # If the agent just created a tool, refresh the tool list.
                if tool_name == "tool_create":
                    custom_schemas = svc.self_improver.custom_tool_schemas()
                    all_tools = build_tool_list(user_message, wm.render_for_prompt() if wm else "", custom_schemas)
                tool_duration = (loop.time() - t_tool0) * 1000
                session_logger.log("tool_call_result", {
                    "tool": tool_name, "duration_ms": tool_duration,
                    "result_keys": list(tool_result.keys()) if isinstance(tool_result, dict) else None,
                })

                # Procedure tracking: log validation results.
                if tool_name in ("vault_lint", "safe_write", "code_run"):
                    try:
                        v_result, v_category, v_details = interpret_validation_result(
                            tool_name, tool_result)
                        proc_name = procedures_in_context[0] if procedures_in_context else "no_procedure"
                        _task_desc = tool_name
                        svc.procedure_tracker.log_result(
                            procedure=proc_name,
                            task=_task_desc,
                            validation_result=v_result,
                            validation_tool=tool_name,
                            error_details=v_details,
                            category=v_category,
                        )
                    except Exception as e:  # noqa: BLE001
                        session_logger.log("procedure_tracking_failed", {"error": str(e)})
                await svc.manager.send_personal_message(json.dumps({
                    "type": "tool_result", "tool": tool_name,
                    "summary": tool_result_summary(tool_name, tool_result),
                }), websocket, session_logger=session_logger)

                # Cap the tool result before appending.
                capped_result = truncate_tool_result(tool_result)
                # --- Pre-digest large tool results (thinking models only) -----
                # A THINKING/reasoning model (nemotron, o1, qwq, r1, ...) handed
                # a huge raw file blob tends to ECHO it char-by-char in its
                # reasoning field and exhaust its output budget before writing
                # the answer (see session 9450d6ad). For those models we inject a
                # compact structural digest instead of the raw blob.
                #
                # NON-thinking models (glm-5.2:cloud, etc.) get the raw content:
                # they have large context windows and don't echo into a reasoning
                # field. Digesting their results HIDES the content they asked
                # for — they can't read their own code, loop calling code_read
                # repeatedly, and stall (session 0ac1b764). truncate_tool_result
                # above already bounds the size for context-window safety.
                if _is_thinking_model \
                        and tool_name == "code_read" \
                        and isinstance(capped_result, dict) \
                        and isinstance(capped_result.get("content"), str) \
                        and len(capped_result["content"]) > 1500:
                    capped_result = _digest_code_read(capped_result)
                # --- Small-model digest for non-code tool results --------------
                # Same gate: only digest for thinking models that would echo raw
                # content into reasoning. Non-thinking models get the raw result
                # (already capped by truncate_tool_result). The small-model
                # digest has a hallucination guard (word-overlap check); if it
                # fails, the raw result passes through untouched.
                if _is_thinking_model \
                        and tool_name != "code_read" \
                        and isinstance(capped_result, dict) \
                        and isinstance(capped_result.get("content"), str) \
                        and len(capped_result["content"]) > 3000:
                    capped_result = _small_model_digest(capped_result, session_logger)
                # --- Aggressive digest for NON-thinking models (opt-in) ------
                # When VAULTBOT_AGGRESSIVE_DIGEST=on, digest very large tool
                # results even for non-thinking cloud models. This prevents
                # several 2.5K-token results from accumulating in history and
                # being re-billed every round. The small-model digest has a
                # hallucination guard; if it fails, the raw result passes
                # through. The digest message tells the model to re-read with
                # narrower parameters if it needs details.
                _aggressive_digest = os.getenv("VAULTBOT_AGGRESSIVE_DIGEST", "off").lower() in ("on", "1", "true", "yes")
                if _aggressive_digest \
                        and not _is_thinking_model \
                        and tool_name != "code_read" \
                        and isinstance(capped_result, dict) \
                        and isinstance(capped_result.get("content"), str) \
                        and len(capped_result["content"]) > int(os.getenv("VAULTBOT_AGGRESSIVE_DIGEST_THRESHOLD", "4000")):
                    capped_result = _small_model_digest(capped_result, session_logger)
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "content": json.dumps(capped_result, default=str),
                })
                # Record for the chat-loop checkpoint.
                _turn_tool_history.append({
                    "round": round_idx,
                    "tool": tool_name,
                    "result_summary": (tool_result_summary(tool_name, tool_result) or "")[:200],
                })

            # --- Read-loop tracking: result-based write success ---
            # Now that all tools have executed, check whether any write tool
            # ACTUALLY wrote. A failed write (execute_procedure 0 steps,
            # vault_safe_write rejected) does NOT reset the read counter —
            # this is the fix for the 60-round loop where the model kept
            # calling execute_procedure(0 steps) and the old code counted it
            # as a write, resetting the read counter each time.
            _round_succeeded_write = False
            _round_failed_write = False
            for tc in round_tool_calls:
                _tn = (tc.get("function", {}) or {}).get("name", "")
                if _tn in _WRITE_TOOLS:
                    # Find this tool's result — re-derive from the last
                    # appended tool message with this tool_name.
                    _tr = None
                    for _m in reversed(conversation):
                        if _m.get("role") == "tool" and _m.get("tool_name") == _tn:
                            try:
                                _tr = json.loads(_m.get("content", "{}"))
                            except (json.JSONDecodeError, TypeError):
                                _tr = {}
                            break
                    if _tool_actually_wrote(_tn, _tr):
                        _turn_write_count += 1
                        _round_succeeded_write = True
                    else:
                        _turn_failed_write_count += 1
                        _round_failed_write = True
                        session_logger.log("failed_write_detected", {
                            "round": round_idx, "tool": _tn,
                            "result_keys": list(_tr.keys()) if isinstance(_tr, dict) else None,
                        })
            # Reset read count only when a write SUCCEEDED (real progress).
            if _round_succeeded_write:
                _turn_read_count = 0
                _read_loop_warned_soft = False
                _read_loop_warned_hard = False
            # Effective read count = total reads − successful writes.
            _turn_effective_read_count = _turn_read_count - _turn_write_count
            if _turn_effective_read_count < 0:
                _turn_effective_read_count = 0
            session_logger.log("read_loop_tracking", {
                "round": round_idx,
                "read_count": _turn_read_count,
                "write_count": _turn_write_count,
                "failed_write_count": _turn_failed_write_count,
                "effective_read_count": _turn_effective_read_count,
                "round_succeeded_write": _round_succeeded_write,
                "round_failed_write": _round_failed_write,
            })

            # --- Findings ledger: append a 1-line entry for this round ---
            # This is the anti-amnesia layer. Each round gets a deterministic
            # 1-line summary of what tool was called and what happened. The
            # ledger is injected into the system prompt every round (above)
            # so the model always sees its progress even when history is
            # truncated by the budget enforcer. Zero LLM cost.
            _round_tools_summary = ", ".join(
                (tc.get("function", {}) or {}).get("name", "?")
                for tc in round_tool_calls
            ) or "(no tools)"
            _round_outcome = "ok"
            if _round_failed_write:
                _round_outcome = "write_failed"
            elif not _round_succeeded_write and _round_has_write_tool:
                _round_outcome = "write_no_effect"
            _finding_entry = f"R{round_idx}: {_round_tools_summary} → {_round_outcome}"
            if round_text.strip():
                _finding_entry += f" | text: {round_text.strip()[:80]}"
            _findings.append(_finding_entry)
            session_logger.log("findings_ledger_updated", {
                "round": round_idx,
                "entry": _finding_entry,
                "total_findings": len(_findings),
            })

            # --- Go-find-out system message injection ------------------------
            # If go-find-out fired this round, inject the research results as a
            # system message AFTER the tool results are appended. A system
            # message is more authoritative than a tool result — the model
            # treats it as framework-level instruction and can't ignore it.
            # This is the fix for the model ignoring go-find-out research
            # results that were only in the tool result.
            if _go_find_out_msg:
                conversation.append({
                    "role": "system",
                    "content": _go_find_out_msg,
                })
                session_logger.log("go_find_out_system_msg_injected", {
                    "round": round_idx,
                    "msg_chars": len(_go_find_out_msg),
                })
                # Clear so it only fires once.
                _go_find_out_msg = ""

            # --- Sliding window (mid-loop) ---
            _pre_mid_len = len(conversation)
            _mid_ctx_win = 0
            try:
                _mid_ctx_win = svc.ollama_client.context_window(
                    svc.ollama_client.llm_model)
            except Exception as e:  # noqa: BLE001 — probe failure is non-fatal; log + use 0 so the sliding window applies its default guard
                session_logger.log("context_window_probe_failed", {
                    "where": "mid_loop", "round": round_idx, "error": str(e)})
            conversation = _apply_sliding_window(
                conversation, context_window_tokens=_mid_ctx_win)
            if len(conversation) < _pre_mid_len:
                session_logger.log("mid_loop_sliding_window", {
                    "round": round_idx,
                    "messages_before": _pre_mid_len,
                    "messages_after": len(conversation),
                })

            # History budget (mid-loop): cap history tokens to prevent
            # unbounded growth re-billed to the cloud every round.
            _pre_mid_budget_len = len(conversation)
            conversation = _enforce_history_budget(
                conversation, session_logger=session_logger)
            if len(conversation) < _pre_mid_budget_len:
                session_logger.log("mid_loop_history_budget", {
                    "round": round_idx,
                    "messages_before": _pre_mid_budget_len,
                    "messages_after": len(conversation),
                })

            # Loop back.
            round_idx += 1

            # --- Read-loop detector: progressive urgency nudges ---
            # If the model has called many read tools without successful
            # writes, escalate from soft nudge → hard warning → force stop.
            #
            # IMPORTANT: these nudges only fire when there is NO plan. A
            # planned research/architecture step can legitimately read dozens
            # of files before writing; nudging "STOP READING" mid-research is
            # exactly what kills long-horizon work. When a plan exists, the
            # per-step state machine (not these counters) governs progress.
            # Uses _turn_effective_read_count (reads − successful writes) so
            # a failed write does NOT reset the counter.
            if (not wm.has_plan()
                    and _turn_effective_read_count >= 15
                    and not _read_loop_forced):
                _read_loop_forced = True
                # Build a diagnostic file list from _seen_content so the
                # nudge tells the model EXACTLY what it already has, not
                # a generic "stop reading" (which the model ignores when
                # it's in a search-anxiety loop). This is the fix for the
                # Dream-Pass-Audit loop: the model kept searching because
                # it didn't recognize that its searches were returning
                # files it already had.
                _seen_list = []
                for _fp, _info in list(_seen_content.items())[:15]:
                    _stem = Path(_fp).stem if _fp else "?"
                    _src = _info.get("source", "?")
                    _seen_list.append(f"  - {_stem} ({_src})")
                _seen_summary = "\n".join(_seen_list) if _seen_list else "  (none)"
                conversation.append({
                    "role": "system",
                    "content": (
                        f"# ACTION REQUIRED: You have {_turn_effective_read_count} "
                        f"effective reads (reads minus successful writes) this "
                        f"turn with no plan and no successful edits. "
                        f"You appear to be stuck.\n\n"
                        f"Files you have ALREADY retrieved this turn:\n"
                        f"{_seen_summary}\n\n"
                        f"You already have this information — STOP SEARCHING. "
                        f"Do NOT call vault_search again. You must now either:\n"
                        f"1. Call plan_task to structure your work, then continue, OR\n"
                        f"2. Call safe_write or code_run to implement a fix, OR\n"
                        f"3. Produce your final answer explaining what you found.\n"
                        f"(If you are legitimately deep-reading as part of planned "
                        f"work, call plan_task first so the framework knows.)"
                    ),
                })
                session_logger.log("read_loop_force", {
                    "round": round_idx,
                    "effective_read_count": _turn_effective_read_count,
                    "read_count": _turn_read_count,
                    "write_count": _turn_write_count,
                    "failed_write_count": _turn_failed_write_count,
                    "seen_files": [Path(fp).stem for fp in list(_seen_content.keys())[:15]],
                })
            elif (not wm.has_plan()
                    and _turn_effective_read_count >= 12
                    and not _read_loop_warned_hard):
                _read_loop_warned_hard = True
                _seen_list_h = []
                for _fp, _info in list(_seen_content.items())[:10]:
                    _stem = Path(_fp).stem if _fp else "?"
                    _src = _info.get("source", "?")
                    _seen_list_h.append(f"  - {_stem} ({_src})")
                _seen_summary_h = "\n".join(_seen_list_h) if _seen_list_h else "  (none)"
                conversation.append({
                    "role": "system",
                    "content": (
                        f"# WARNING: You have {_turn_effective_read_count} "
                        f"effective reads without any successful edits. "
                        f"Files already retrieved:\n{_seen_summary_h}\n\n"
                        f"You have enough context to act. "
                        f"Call safe_write to implement your fix now, or explain "
                        f"what's blocking you. Do not read more files."
                    ),
                })
                session_logger.log("read_loop_warn_hard", {
                    "round": round_idx,
                    "effective_read_count": _turn_effective_read_count,
                    "read_count": _turn_read_count,
                    "write_count": _turn_write_count,
                    "failed_write_count": _turn_failed_write_count,
                    "seen_files": [Path(fp).stem for fp in list(_seen_content.keys())[:10]],
                })
            elif (not wm.has_plan()
                    and _turn_effective_read_count >= 8
                    and not _read_loop_warned_soft):
                _read_loop_warned_soft = True
                conversation.append({
                    "role": "system",
                    "content": (
                        f"# NOTICE: You have {_turn_effective_read_count} "
                        f"effective reads without any successful changes. "
                        f"Consider whether you have enough information to "
                        f"write a fix. If you do, call safe_write. "
                        f"If you're stuck, explain what you need."
                    ),
                })
                session_logger.log("read_loop_warn_soft", {
                    "round": round_idx,
                    "effective_read_count": _turn_effective_read_count,
                    "read_count": _turn_read_count,
                    "write_count": _turn_write_count,
                    "failed_write_count": _turn_failed_write_count,
                })

            # --- Convergence nudge (cost control) ---------------------------
            # After a configurable number of rounds, tell the model it's
            # approaching the limit so it stops calling tools and produces
            # its final answer. This prevents runaway loops from burning
            # _MAX_ROUNDS × (big prompt) in cloud tokens for no benefit.
            if round_idx == _CONVERGENCE_NUDGE_ROUND:
                conversation.append({
                    "role": "system",
                    "content": (
                        f"# BUDGET NOTICE: You have used {round_idx} of "
                        f"{_MAX_ROUNDS} rounds. If you have enough information, "
                        f"produce your final answer now. Only call tools if "
                        f"you critically need more data."),
                })
                session_logger.log("convergence_nudge", {
                    "round": round_idx,
                    "max_rounds": _MAX_ROUNDS,
                })

            # Chat-loop checkpoint: snapshot the in-flight turn.
            # Includes the findings ledger so a restart mid-turn restores
            # the model's progress awareness, not just the partial answer.
            if _cp is not None:
                try:
                    _cp.save({
                        "user_message": user_message,
                        "round_idx": round_idx,
                        "accumulated": final_answer,
                        "thinking": thinking_text,
                        "tool_history": _turn_tool_history,
                        "working_memory": snapshot_working_memory(wm),
                        "findings_ledger": list(_findings),
                    })
                    session_logger.log("checkpoint_saved", {
                        "round": round_idx,
                        "findings_count": len(_findings),
                        "plan_has_tasks": wm.has_plan(),
                    })
                except Exception as e:  # noqa: BLE001 — checkpoint is best-effort; the chat loop must not crash on save failure
                    session_logger.log("chat_checkpoint_save_failed", {"error": str(e)})

            # Stream history persistence: save the conversation-so-far to
            # disk after each tool round so a crash mid-turn doesn't lose
            # the entire turn's context. Best-effort: never breaks the loop.
            try:
                _hist_so_far = []
                for _m in conversation:
                    if _m.get("role") == "system":
                        continue
                    _m2 = dict(_m)
                    _m2.pop("thinking", None)
                    _c = _m2.get("content")
                    if isinstance(_c, str) and len(_c) > 4000:
                        _m2["content"] = _c[:4000] + "\n[...truncated in history...]"
                    _hist_so_far.append(_m2)
                if len(_hist_so_far) > 0:
                    save_history(_hist_so_far)
            except Exception as _e:  # noqa: BLE001 — stream history is best-effort; the loop must not crash on save failure
                session_logger.log("stream_history_save_failed", {"error": str(_e)})

            # --- Round summary (diagnostic logging) ---
            # One event per round with everything needed to understand it
            # without reading any other event. Grep "round_summary" to
            # reconstruct a stalled session from the session log alone.
            session_logger.log("round_summary", {
                "round": round_idx,
                "tools_called": [tc.get("function", {}).get("name", "?")
                                 for tc in round_tool_calls],
                "tool_count": len(round_tool_calls),
                "text_chars": len(round_text),
                "thinking_chars": len(round_thinking),
                "read_count": _turn_read_count,
                "write_count": _turn_write_count,
                "failed_write_count": _turn_failed_write_count,
                "effective_read_count": _turn_effective_read_count,
                "plan_gate_active": _plan_gate_active,
                "has_plan": wm.has_plan(),
                "findings_count": len(_findings),
                "conv_chars": sum(len(str(m.get("content", "") or ""))
                                  for m in conversation),
                "conv_msgs": len(conversation),
            })

         # Round-cap safety log: if we exited the loop by hitting _MAX_ROUNDS
         # (not via a natural break), record it so it's visible in session logs.
         if round_idx >= _MAX_ROUNDS:
            session_logger.log("round_cap_hit", {
                "round": round_idx,
                "answer_length": len(final_answer),
                "tool_rounds": _tool_rounds_executed,
            })
            session_logger.log("loop_exit", {
                "reason": "max_rounds",
                "round": round_idx,
                "total_tools": _tool_rounds_executed,
                "total_text_chars": len(final_answer),
                "findings_count": len(_findings),
                "effective_read_count": _turn_effective_read_count,
                "failed_write_count": _turn_failed_write_count,
            })

        except Exception as e:  # noqa: BLE001
            session_logger.log_exception(e, context="handle_chat_agentic_loop")
            write_partial(partial_path, user_message, final_answer, thinking_text)
            session_logger.log("partial_answer_saved_on_crash", {
                "partial_path": str(partial_path),
                "answer_chars": len(final_answer),
            })
            raise
        finally:
            # If the answer completed normally, clean up the partial file.
            if final_answer and len(final_answer) > 50:
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except Exception as e:  # noqa: BLE001
                    session_logger.log("partial_cleanup_failed", {"error": str(e)})

        session_logger.log("llm_generate", {
            "model": svc.ollama_client.llm_model,
            "stream": True,
            "total_chunks": total_chunks,
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
            "duration_ms": (loop.time() - t0) * 1000,
        })

        # --- Token cost tracking: log and emit cumulative per-turn totals ---
        _turn_token_totals["rounds"] = round_idx + 1
        # Fallback estimate: if the cloud backend didn't return eval_stats
        # (Ollama Cloud proxy doesn't), estimate tokens from the final
        # conversation + the answer/thinking text using chars/4 heuristic.
        if _turn_token_totals["prompt_tokens"] == 0:
            _est_prompt = sum(
                len(str(m.get("content", "") or ""))
                for m in _model_conversation if isinstance(m, dict)
            ) // 4
            _turn_token_totals["prompt_tokens"] = _est_prompt
        if _turn_token_totals["completion_tokens"] == 0:
            _est_completion = (len(final_answer) + len(thinking_text)) // 4
            _turn_token_totals["completion_tokens"] = max(1, _est_completion)
        _turn_token_totals["total_tokens"] = (
            _turn_token_totals["prompt_tokens"]
            + _turn_token_totals["completion_tokens"])
        _turn_token_totals["estimated"] = True  # flag that these are estimates
        session_logger.log("token_usage", _turn_token_totals)
        # Persist to the session-level accumulator for cross-turn totals.
        try:
            session_logger.add_token_usage(
                _turn_token_totals["prompt_tokens"],
                _turn_token_totals["completion_tokens"])
        except Exception:  # noqa: BLE001 — best-effort
            pass
        try:
            await svc.manager.send_personal_message(json.dumps({
                "type": "token_usage",
                "prompt_tokens": _turn_token_totals["prompt_tokens"],
                "completion_tokens": _turn_token_totals["completion_tokens"],
                "total_tokens": _turn_token_totals["total_tokens"],
                "rounds": _turn_token_totals["rounds"],
            }), websocket, session_logger=session_logger)
        except Exception as _e:  # noqa: BLE001
            session_logger.log("token_usage_emit_failed", {"error": str(_e)})

        await svc.manager.send_personal_message(json.dumps({"type": "answer_done", "content": final_answer}), websocket, session_logger=session_logger)
        # Turn completed normally — clear the chat-loop checkpoint.
        if _cp is not None:
            try:
                _cp.clear()
            except Exception as e:  # noqa: BLE001
                session_logger.log("checkpoint_clear_failed", {"error": str(e)})
        # Refresh the token meter after the full turn.
        try:
            _total_chars = sum(len(str(m.get("content", "") or ""))
                               for m in conversation if isinstance(m, dict))
            _used_tokens = max(1, _total_chars // 4)
            _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
            await svc.manager.send_personal_message(json.dumps({
                "type": "context_usage",
                "model": svc.ollama_client.llm_model,
                "context_window": _ctx_window,
                "used_tokens": _used_tokens,
                "available_tokens": max(0, _ctx_window - _used_tokens),
                "messages": len(conversation),
            }), websocket, session_logger=session_logger)
        except Exception as _e:  # noqa: BLE001
            session_logger.log("context_usage_emit_failed", {"error": str(_e)})
        session_logger.log("chat_end", {
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
        })

        # --- Notify the Obsidian plugin that vault files may have changed ---
        try:
            import time as _time
            changed_files = []
            vault_root = svc.vault_path
            for dirpath, dirnames, filenames in os.walk(vault_root):
                dirnames[:] = [d for d in dirnames if d not in (
                    '.obsidian', 'vaultbot_stuff/vaultbot_backend', 'node_modules', '.git',
                    'vaultbot_stuff/learningMaterial', 'custom_tools', '__pycache__',
                )]
                for fname in filenames:
                    if fname.endswith('.md'):
                        fpath = os.path.join(dirpath, fname)
                        try:
                            mtime = os.path.getmtime(fpath)
                            if mtime >= chat_start_time:
                                rel = os.path.relpath(fpath, vault_root)
                                changed_files.append(rel.replace(os.sep, '/'))
                        except OSError:
                            pass
            if changed_files:
                await svc.manager.send_personal_message(
                    json.dumps({"type": "vault_changed", "files": changed_files}),
                    websocket, session_logger=session_logger)
                session_logger.log("vault_changed_broadcast", {
                    "file_count": len(changed_files),
                })
        except Exception as e:  # noqa: BLE001
            session_logger.log("vault_changed_failed", {"error": str(e)})

        # Embedding-drift feedback: nudge the stored embeddings of retrieved
        # notes toward (or away from) this query based on whether the context
        # was useful.
        if retrieved_paths:
            try:
                first_round_researched = (round_idx > 0 and len(final_answer) < 200)
                q_emb = await loop.run_in_executor(
                    None, svc.vault_indexer._get_embedding, user_message)
                top_fp = retrieved_paths[0]
                if first_round_researched:
                    svc.embedding_drift.record_feedback(top_fp, q_emb, helpful=False)
                elif len(final_answer) > 50:
                    svc.embedding_drift.record_feedback(top_fp, q_emb, helpful=True)
                session_logger.log("drift_feedback", {
                    "top_note": Path(top_fp).stem,
                    "helpful": (len(final_answer) > 50 and not first_round_researched),
                    "answer_len": len(final_answer),
                    "rounds": round_idx + 1})
            except Exception as e:  # noqa: BLE001
                session_logger.log("drift_feedback_failed", {"error": str(e)})

        # Lazy de-fluff: after the answer is delivered, condense any retrieved
        # notes that have crossed the touch threshold.
        if retrieved_paths:
            async def _run_lazy_condense_bg():
                try:
                    summary = await loop.run_in_executor(
                        None, svc.lazy_condenser.condense_batch, retrieved_paths)
                    if not summary.get("condensed"):
                        return
                    session_logger.log("lazy_condense_done", summary)
                    from lazy_condenser import CONDENSE_MARKER
                    condensed_paths = []
                    for fp in retrieved_paths:
                        try:
                            if CONDENSE_MARKER in Path(fp).read_text(
                                    encoding="utf-8", errors="replace"):
                                condensed_paths.append(fp)
                        except Exception:  # noqa: BLE001
                            continue
                    if not condensed_paths:
                        return
                    _n, new_embs = await loop.run_in_executor(
                        None, svc.vault_indexer.batch_add_files,
                        condensed_paths, True)
                    title_map = existing_note_titles(svc)
                    for fp in condensed_paths:
                        try:
                            await loop.run_in_executor(
                                None, link_outbound, fp, title_map)
                        except Exception as e:  # noqa: BLE001
                            session_logger.log("post_condense_linkoutbound_failed",
                                {"path": fp, "error": str(e)})
                    source_keys = {str(Path(fp).resolve()) for fp in condensed_paths}
                    try:
                        cross = await loop.run_in_executor(
                            None, cross_link_textbooks, svc,
                            condensed_paths, new_embs, source_keys)
                        session_logger.log("post_condense_relink", {
                            "condensed": len(condensed_paths),
                            "cross_links": cross.get("cross_links_added", 0),
                        })
                    except Exception as e:  # noqa: BLE001
                        session_logger.log("post_condense_crosslink_failed",
                                           {"error": str(e)})
                    try:
                        from concept_card import (
                            build_card_for,
                            card_path_for,
                            needs_refine,
                            refine_card,
                        )
                        for fp in condensed_paths:
                            card = card_path_for(fp)
                            if card.exists():
                                try:
                                    old = card.read_text(
                                        encoding="utf-8", errors="replace")
                                    from concept_card import REFINED_MARKER
                                    if REFINED_MARKER not in old:
                                        build_card_for(fp, vault_graph=svc.vault_graph)
                                except Exception as e:  # noqa: BLE001
                                    session_logger.log("card_rebuild_failed",
                                        {"path": fp, "error": str(e)})
                            try:
                                svc.embedding_drift.reset(fp)
                                if card.exists():
                                    svc.embedding_drift.reset(str(card))
                            except Exception as e:  # noqa: BLE001
                                session_logger.log("drift_reset_failed",
                                    {"path": fp, "error": str(e)})
                        refined = 0
                        for fp in retrieved_paths:
                            card = card_path_for(fp)
                            if not card.exists():
                                continue
                            try:
                                tc = svc.lazy_condenser.touch_counts.get(
                                    str(Path(card).resolve()), 0)
                            except Exception:  # noqa: BLE001
                                tc = 0
                            if needs_refine(card, tc):
                                r = await loop.run_in_executor(
                                    None, refine_card, card, svc.ollama_client, None)
                                if r.get("refined"):
                                    refined += 1
                                    await loop.run_in_executor(
                                        None, svc.vault_indexer.batch_add_files,
                                        [str(card)], False)
                                    try:
                                        svc.embedding_drift.reset(str(card))
                                    except Exception as e:  # noqa: BLE001
                                        session_logger.log("drift_reset_failed",
                                            {"card": str(card),
                                             "error": str(e)})
                        if refined:
                            session_logger.log("card_refine_done",
                                               {"refined": refined})
                    except Exception as e:  # noqa: BLE001
                        session_logger.log("card_refine_failed",
                                           {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    session_logger.log("lazy_condense_bg_failed", {"error": str(e)})
                    await notify_problem(svc, websocket, e,
                        context={"stage": "condensing notes in the background"},
                        user_message=(
                            "Something went wrong while condensing long "
                            "notes in the background. This won't affect "
                            "your chat — your notes are safe."),
                        remedy_hint="")
            asyncio.create_task(_run_lazy_condense_bg())

        # --- QA idle worker: fix note frontmatter while the user reads ---
        # After the answer is delivered, the user spends time reading and
        # typing their next message.  This idle window is when the QA worker
        # pulls notes from a priority queue (most-used first) and fixes
        # weak frontmatter (missing fields, weak summaries, generic tags).
        # The worker is interrupted the moment the user sends a new message
        # — in-flight note is completed, unprocessed notes stay queued.
        async def _run_qa_idle_bg():
            try:
                from qa_worker import run_qa_idle_window
                _qa_ollama = getattr(svc, "ollama_client", None)
                # Use the small model if available (cheaper for metadata gen)
                try:
                    from llm_client import get_small_client
                    _qa_ollama = get_small_client() or _qa_ollama
                except Exception:  # noqa: BLE001
                    pass
                _qa_summary = await run_qa_idle_window(
                    vault_root=svc.vault_path,
                    ollama_client=_qa_ollama,
                    logger=lambda msg: session_logger.log("qa_worker", {"msg": msg}),
                )
                session_logger.log("qa_idle_window_done", _qa_summary)
            except Exception as e:  # noqa: BLE001
                session_logger.log("qa_idle_bg_failed", {"error": str(e)})
        asyncio.create_task(_run_qa_idle_bg())

        # Persist this turn into the per-session history.
        try:
            history = getattr(websocket, "conversation_history", None)
            if history is not None:
                new_turns = []
                for m in conversation:
                    if m.get("role") == "system":
                        continue
                    m2 = dict(m)
                    m2.pop("thinking", None)
                    c = m2.get("content")
                    if isinstance(c, str) and len(c) > 4000:
                        m2["content"] = c[:4000] + "\n[...truncated in history...]"
                    new_turns.append(m2)
                if len(new_turns) > len(history):
                    websocket.conversation_history = new_turns
                    session_logger.log("history_persisted", {
                        "turns": len(new_turns),
                        "history_chars": sum(len(str(m.get("content", ""))) for m in new_turns),
                        "final_answer_len": len(final_answer or ""),
                    })
                    save_history(new_turns)
                # Index this turn in the conversation index so future queries
                # can retrieve it (conversation-aware retrieval).  Only
                # index when there's a real answer — a tool-only or empty
                # turn isn't useful for recall.
                if final_answer and len(final_answer) > 20:
                    try:
                        _conv_idx = getattr(svc, "conversation_index", None)
                        if _conv_idx is not None:
                            _conv_idx.add_turn(user_message, final_answer)
                    except Exception as _e:  # noqa: BLE001
                        session_logger.log("conversation_index_add_failed",
                            {"error": str(_e)})
                # Persist working memory to disk so the plan survives
                # restarts.  Only save when there's an active plan.
                try:
                    if wm.has_plan():
                        wm.save_to_disk()
                except Exception as _e:  # noqa: BLE001
                    session_logger.log("wm_save_disk_failed",
                        {"error": str(_e)})
        except Exception as e:  # noqa: BLE001
            session_logger.log("history_persist_failed", {"error": str(e)})
            await notify_problem(svc, websocket, e,
                context={"category": "history_lost",
                         "stage": "persisting chat history"},
                user_message=(
                    "I couldn't save our conversation history. "
                    "If I restart, I won't remember this chat."),
                remedy_hint="Check disk space and file permissions.")

        # Save a chat note if the answer is substantive.
        if len(final_answer) > 100:
            try:
                note_path = await loop.run_in_executor(None, svc.note_creator.create_note_from_chat, user_message, final_answer, thinking_text)
                session_logger.log("chat_note_created", {"note_path": note_path})
            except Exception as e:  # noqa: BLE001
                session_logger.log_exception(e, context="note_creator.create_note_from_chat")
                print(f"Error creating chat note: {e}")

        # Close the MIRROR loop: regenerate the bounded self-model from this
        # turn's activity.
        try:
            activity_parts = [f"User asked: {user_message[:300]}"]
            if final_answer:
                activity_parts.append(f"Answer: {final_answer[:500]}")
            else:
                activity_parts.append("Answer: (empty — model produced no final text)")
            _tool_summary = []
            for m in conversation:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        _tool_summary.append(fn.get("name", "?"))
            if _tool_summary:
                activity_parts.append(
                    "Tools used: " + ", ".join(_tool_summary[:10]))
            activity = "\n".join(activity_parts)
            _regen_result = await loop.run_in_executor(
                None, lambda: svc.identity.regenerate_self_model(activity))
            if _regen_result and not _regen_result.startswith("I am VaultBot"):
                session_logger.log("self_model_regen_skipped_or_done", {
                    "turns_since_regen": getattr(
                        svc.identity, "_turns_since_regen", 0)})
        except Exception as e:  # noqa: BLE001
            session_logger.log("self_model_regenerate_failed", {"error": str(e)})

        # Pattern extraction: check for new consolidation gaps after each chat.
        try:
            _gaps = await loop.run_in_executor(
                None, svc.pattern_extractor.get_consolidation_gaps)
            if _gaps:
                session_logger.log("consolidation_gaps", {
                    "gap_count": len(_gaps),
                    "top_gaps": [
                        {"kind": g["kind"], "topic": g["topic"],
                         "priority": g.get("priority", 0)}
                        for g in _gaps[:5]
                    ],
                })
        except Exception as e:  # noqa: BLE001
            session_logger.log("pattern_extraction_failed", {"error": str(e)})
    finally:
        svc.autonomous_researcher.resume_after_chat()


async def execute_agent_tool(svc: Services, tool_name: str, args: dict[str, Any],
                             session_logger, websocket: WebSocket | None = None,
                             user_message: str = "") -> dict[str, Any]:
    """Execute one tool call from the chat LLM. Runs in the async context.

    `websocket` is passed so long-running tools (vault_research) can push
    live progress events to the UI instead of going silent for 30-60s.
    """
    # Module-level imports from chat_helpers, weaving — no longer deferred
    # from main (circular dependency eliminated).
    loop = asyncio.get_event_loop()
    session_logger.log("execute_agent_tool_body_start", {
        "tool": tool_name, "t_ms": loop.time() * 1000,
    })

    if tool_name == "vault_research":
        topic = (args.get("topic") or "").strip()
        depth = args.get("depth", "deep")
        if not topic:
            return {"error": "missing topic"}

        # SUBAGENT CONTEXT ISOLATION (Copilot runSubagent / Claude subagent
        # pattern): run the full dig + note creation in a SEPARATE PROCESS so
        # the verbose work (1900+ source-rejection events, scrapes, a 50K
        # synthesis) never enters the orchestrator's conversation. The
        # subprocess prints ONLY a compact JSON brief to stdout; the chat
        # loop gets one bounded tool result, not a flood. The full synthesis
        # stays on disk in the created note — re-readable via vault_search /
        # web_read_source. See subagent.py.
        #
        # Fallback: if VAULTBOT_SUBAGENT=off (or the import fails), the
        # in-process path runs below. It still distills the report to a
        # bounded brief before returning, so the conversation never balloons
        # either way. The subagent path is strictly safer (hard process
        # isolation); the in-process path is the safety net.
        try:
            from subagent import subagent_enabled, run_research_subagent
            _use_subagent = subagent_enabled()
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            _use_subagent = False

        if _use_subagent:
            session_logger.log("subagent_research_invoked", {"topic": topic[:80]})
            # Emit heartbeats while the subprocess runs — the child can't send
            # websocket events (it's a separate process), so the orchestrator
            # keeps the UI alive with the existing run_with_heartbeat pattern.
            t_sub = loop.time()
            try:
                brief = await run_with_heartbeat(
                    svc, websocket, f"research{topic[:40]}",
                    run_research_subagent, topic, depth, session_logger)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log_exception(e, context="subagent_research")
                brief = {"status": "error",
                          "error": f"subagent research failed: {e}",
                          "subagent": True}
            # The subagent already created + indexed the note. Refresh the
            # orchestrator's in-memory graph so subsequent rounds see it
            # (the child's indexer is its own instance).
            try:
                await loop.run_in_executor(None, svc.vault_graph.refresh)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log("post_subagent_graph_refresh_failed",
                                    {"error": str(e)})
            session_logger.log("subagent_research_complete", {
                "duration_ms": int((loop.time() - t_sub) * 1000),
                "status": brief.get("status"),
                "source_count": brief.get("source_count", 0),
                "note_path": brief.get("note_path"),
            })
            # Normalize: an empty-status brief becomes an error so the model
            # doesn't treat a no-sources dig as a real result.
            if brief.get("status") == "empty":
                brief["error"] = brief.get("error", "no web sources found")
            # Attach the goal hint so the agent can decide whether to record
            # this research against a larger task (same hint as the
            # in-process path — the subagent is stateless and can't call
            # set_goal itself).
            brief["goal_hint"] = (
                "If this research advances a multi-step task, consider "
                "calling set_goal to record the current goal + next step "
                "so you stay on track across turns. If this was a one-off, "
                "ignore this."
            )
            return brief

        # --- In-process fallback (VAULTBOT_SUBAGENT=off or import fail) ---
        # Kept as a safety net. Still distills the report to a bounded brief
        # so the conversation never balloons. The subagent path above is
        # preferred (hard isolation); this path shares the loop.
        if depth == "quick":
            svc.research_engine.max_rounds = 1
            svc.research_engine.max_follow_ups = 0

        prev_cb = svc.research_engine.progress_callback
        if websocket is not None:
            def _progress_cb(stage: str, detail: dict):
                try:
                    asyncio.run_coroutine_threadsafe(
                        send_progress(svc, websocket, stage, detail), loop)
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    session_logger.log("tool_progress_cb_failed",
                        {"error": str(e)})
            svc.research_engine.progress_callback = _progress_cb

        t_research = loop.time()
        try:
            report = await run_with_heartbeat(
                svc, websocket, f"research{topic[:40]}", svc.research_engine.research, topic)
        finally:
            svc.research_engine.max_rounds = int(os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4"))
            svc.research_engine.max_follow_ups = int(os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3"))
            svc.research_engine.progress_callback = prev_cb
            session_logger.log("agent_research_done", {
                "duration_ms": (loop.time() - t_research) * 1000,
                "source_count": report.get("source_count", 0) if isinstance(report, dict) else 0,
            })
        # Persist a linked note so the research becomes vault knowledge.
        if report.get("source_count") and report.get("synthesis"):
            try:
                summary = (f"Research into '{topic}' ({report['source_count']} "
                           f"sources, {report['synthesis_facts']} facts).")
                await send_progress(svc, websocket, "writing_note", {"topic": topic})
                note_path = await run_with_heartbeat(
                    svc, websocket, "writing_note",
                    svc.note_creator.create_note_from_research,
                    topic, report["synthesis"], summary)
                if report.get("llm_synthesized"):
                    # LLM synthesis already produced a structured note with
                    # frontmatter, H2 prose sections, wikilinks, and Sources.
                    # Write it directly -- skip double-processing.
                    try:
                        Path(note_path).write_text(
                            report["synthesis"], encoding="utf-8")
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log("research_note_write_failed",
                            {"path": note_path, "error": str(e)})
                else:
                    # Extractive fallback: wrap in markdown, then try LLM
                    # structuring (ONE call) for frontmatter + H2 sections.
                    md = svc.research_engine.synthesize_note_markdown(report, summary)
                    Path(note_path).write_text(md, encoding="utf-8")
                    _titles = svc.research_engine._get_vault_note_titles(svc.vault_path)
                    _structured = svc.research_engine.synthesize_structured_note(
                        report, summary, ollama_client=svc.ollama_client,
                        vault_note_titles=_titles)
                    if _structured and len(_structured) >= svc.research_engine._STRUCTURED_MIN_CHARS:
                        Path(note_path).write_text(_structured, encoding="utf-8")
                        session_logger.log("research_note_structured",
                                           {"note_path": note_path,
                                            "chars": len(_structured)})
                report["note_path"] = note_path
            except Exception as e:
                session_logger.log_exception(e, context="agent_research_note")
        # A-MEM: evolve neighboring notes' tags/links so the vault learns from
        # the new note (arXiv:2502.12110).
        if report.get("note_path"):
            try:
                await send_progress(svc, websocket, "amem_evolve", {
                    "note": Path(report["note_path"]).stem})
                await run_with_heartbeat(
                    svc, websocket, "amem_evolve",
                    lambda: svc.amem.evolve_on_create(
                        report.get("note_path", ""), report.get("synthesis", ""),
                        skip_refresh=True))
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log("amem_evolve_failed", {"error": str(e)})
        # Goal hint (same as the subagent path).
        if isinstance(report, dict):
            report["goal_hint"] = (
                "If this research advances a multi-step task, consider "
                "calling set_goal to record the current goal + next step "
                "so you stay on track across turns. If this was a one-off, "
                "ignore this."
            )
        # Distill the full report to a compact brief before returning so the
        # conversation never balloons (the in-process path doesn't have hard
        # isolation, so this is the bound). Same brief shape as the subagent
        # path so the chat loop + truncate_tool_result work unchanged.
        if isinstance(report, dict):
            try:
                _syn = str(report.get("synthesis", "") or "")
                _facts = report.get("synthesis_facts") or []
                if isinstance(_facts, list):
                    _facts_txt = "\n".join(f"- {str(f)[:300]}" for f in _facts[:8])
                else:
                    _facts_txt = str(_facts)[:1500]
                report = {
                    "topic": report.get("topic"),
                    "source_count": report.get("source_count", 0),
                    "note_path": report.get("note_path"),
                    "synthesis_brief": _syn[:1500] + (
                        "\n*[... full synthesis in the note at note_path ...]*"
                        if len(_syn) > 1500 else ""),
                    "key_facts": _facts_txt,
                    "subagent_note": (
                        "Verbose dig output kept OUT of context (subagent "
                        "isolation). Full synthesis is in the created note; "
                        "re-read it via vault_research/web_read_source if you "
                        "need a specific detail."),
                    "goal_hint": report.get("goal_hint", ""),
                }
                session_logger.log("subagent_result_distilled", {
                    "tool": "vault_research",
                    "orig_synthesis_chars": len(_syn),
                    "brief_chars": len(report.get("synthesis_brief", "")),
                })
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass  # distillation is best-effort; never break the tool
        return report

    if tool_name == "vault_search":
        query = args.get("query", "")
        k = int(args.get("k", 5))
        results = await loop.run_in_executor(None, svc.vault_indexer.search, query, k)
        return {"query": query, "results": [
            {"file_path": r.get("file_path"), "content": r.get("content", "")[:1200],
             "score": r.get("score")} for r in results
        ]}

    if tool_name == "vault_read_note":
        title = (args.get("title") or "").strip()
        max_lines = int(args.get("max_lines", 0))
        if not title:
            return {"error": "missing title"}
        def _read_note_by_title():
            # Try the in-memory graph first (fast, covers the common case).
            node = svc.vault_graph.get_note(title)
            file_path = None
            if node and node.get("file_path"):
                file_path = Path(node["file_path"])
            else:
                # Deferred build may not have indexed it yet — fall back
                # to a deterministic path resolve via rglob stem match.
                resolved = svc.vault_graph._resolve_note_path(title)
                if resolved is not None:
                    file_path = resolved
            if file_path is None or not file_path.exists():
                return {"error": f"note not found: {title}"}
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except Exception as e:  # noqa: BLE001
                return {"error": f"read failed: {e}"}
            s = 1
            e = len(lines) if max_lines <= 0 else min(max_lines, len(lines))
            snippet = "\n".join(lines[s - 1:e])
            return {"file_path": str(file_path), "title": file_path.stem,
                    "total_lines": len(lines), "start_line": s,
                    "end_line": e, "content": snippet}
        return await loop.run_in_executor(None, _read_note_by_title)

    if tool_name == "vault_gaps":
        gaps = await loop.run_in_executor(None, svc.autonomous_researcher._identify_gaps)
        return {"gaps": gaps[:20], "count": len(gaps)}

    if tool_name == "vaultbot_status":
        return svc.autonomous_researcher.status()

    # --- Meta-tools (self-improvement) --- #
    if tool_name == "code_read":
        return await loop.run_in_executor(None, lambda: svc.self_improver.code_read(
            args.get("file_path", ""), int(args.get("start_line", 1)),
            int(args.get("end_line", 0))))

    if tool_name == "code_run":
        return await loop.run_in_executor(None, lambda: svc.self_improver.code_run(
            args.get("code", ""), int(args.get("timeout", 15))))

    if tool_name == "tool_create":
        result = await loop.run_in_executor(None, lambda: svc.self_improver.tool_create(
            args.get("tool_name", ""), args.get("description", ""),
            args.get("parameters", {}), args.get("code", "")))
        # Hot-reload so the new tool is callable immediately.
        svc.self_improver.load_custom_tools()
        return result

    if tool_name == "self_reflect":
        ctx = args.get("vault_context", "")
        return await loop.run_in_executor(None, lambda: svc.self_improver.self_reflect(
            args.get("topic", ""), ctx))

    if tool_name == "git_rollback":
        return await loop.run_in_executor(None, lambda: svc.self_improver.git_rollback(
            args.get("file_path", "")))

    if tool_name == "safe_write":
        return await loop.run_in_executor(None, lambda: svc.self_improver.safe_write(
            args.get("file_path", ""), args.get("content", ""),
            bool(args.get("dry_run", False))))

    if tool_name == "js_safe_write":
        return await loop.run_in_executor(None, lambda: svc.self_improver.js_safe_write(
            args.get("file_path", ""), args.get("content", ""),
            bool(args.get("dry_run", False))))

    if tool_name == "capability_audit":
        return await loop.run_in_executor(None, lambda: svc.self_improver.capability_audit(
            args.get("task", "")))

    # --- Procedure execution (step-gate runtime) --- #
    # The LLM calls this to execute a procedure written in a markdown note.
    # The procedure runs as a blocking subprocess: code steps execute
    # deterministically (zero LLM cost), LLM steps use minimal context via
    # get_llm_client(). Returns the procedure's step-by-step output.
    # See [[Procedure-Subprocess-Architecture]].
    if tool_name == "execute_procedure":
        from procedure_compiler import compile_procedure as _compile_proc
        from step_gate_runtime import execute_procedure as _run_proc

        proc_name = args.get("procedure_name", "")
        if not proc_name:
            return {"error": "missing procedure_name"}

        backend_dir = Path(__file__).parent.resolve()
        vault_root = backend_dir.parent

        # Resolve the procedure file via the tracker's stem index (O(1)
        # after first build) instead of rglob-walking the vault on every
        # call.  The index is cached on the tracker and rebuilt lazily if
        # the stem is missing (covers a note written seconds ago).
        proc_file = None
        try:
            idx = getattr(svc.procedure_tracker, "_stem_index", None)
            if idx is None:
                idx = svc.procedure_tracker.get_procedure_index(str(vault_root))
                svc.procedure_tracker._stem_index = idx
            entry = idx.get(proc_name)
            if entry:
                proc_file = Path(entry["path"])
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            session_logger.log("procedure_lookup_failed",
                {"proc": proc_name, "error": str(e)})

        if not proc_file:
            # Fallback: rglob for a just-written note the index hasn't seen.
            for candidate in vault_root.rglob("*.md"):
                if candidate.stem == proc_name:
                    proc_file = candidate
                    break

        if not proc_file:
            return {"error": f"procedure not found: {proc_name}"}

        # --- Execution gate (extra-safe): check the procedure's status BEFORE
        # running it. verified -> run clean; experimental -> run with a caution
        # note; flagged -> BLOCK and route to re-research. This is the
        # deterministic trust layer: a procedure that repeatedly failed
        # validation is never executed, no matter how confidently the model
        # asks for it. See procedure_surface.status_allows_execution.
        try:
            _idx = getattr(svc.procedure_tracker, "_stem_index", None) or {}
            _entry = _idx.get(proc_name) or {}
            _status = str((_entry.get("frontmatter") or {}).get("status", ""))
            _allowed, _gate_reason = status_allows_execution(_status)
            if not _allowed:
                session_logger.log("procedure_blocked", {
                    "procedure": proc_name, "status": _status})
                return {
                    "error": f"procedure blocked: {proc_name}",
                    "status": _status or "unknown",
                    "reason": _gate_reason,
                    "blocked": True,
                }
            _proc_caution = (_gate_reason == "experimental")
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            _proc_caution = False  # gate failure must not block execution

        proc = _compile_proc(str(proc_file))
        if not proc:
            return {"error": f"not a procedure note: {proc_name}"}

        # --- Model cartridge selection --- #
        # Procedures declare model_cartridge: big|small|vision in frontmatter.
        # big    → the main chat LLM (svc.ollama_client, local or cloud)
        # small  → the tiny local model (get_small_client, local-only)
        # vision → the vision model (get_vision_client)
        # If the small/vision client isn't configured, fall back to big so
        # the procedure still runs. This is the "tiny dance partner" design:
        # procedures that don't need the big model's reasoning power delegate
        # to the small one, saving cloud tokens. As more procedures use the
        # small cartridge, the cloud model does less and less.
        _cartridge = getattr(proc, "model_cartridge", "big") or "big"
        _proc_llm_client = svc.ollama_client  # default: big
        _cartridge_note = ""
        try:
            if _cartridge == "small":
                from llm_client import get_small_client
                _small = get_small_client(session_logger)
                if _small is not None:
                    _proc_llm_client = _small
                    _cartridge_note = " (using small model)"
                else:
                    _cartridge_note = " (small model not configured, using big)"
            elif _cartridge == "vision":
                from llm_client import get_vision_client
                _vision = get_vision_client(session_logger)
                if _vision is not None:
                    _proc_llm_client = _vision
                    _cartridge_note = " (using vision model)"
                else:
                    _cartridge_note = " (vision model not configured, using big)"
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            session_logger.log("cartridge_select_failed", {
                "procedure": proc_name, "cartridge": _cartridge, "error": str(e)})
        session_logger.log("procedure_cartridge", {
            "procedure": proc_name, "cartridge": _cartridge,
            "model": getattr(_proc_llm_client, "llm_model", "?"),
        })

        result = await _run_proc(
            procedure=proc,
            context="",
            llm_client=_proc_llm_client,
            vault_path=str(vault_root),
            procedure_tracker=svc.procedure_tracker,
            # Pass the model's tool arguments (minus procedure_name) down
            # so code steps can read them via the injected `args` dict.
            procedure_args={k: v for k, v in args.items() if k != "procedure_name"},
        )

        # --- Procedure-level drift feedback (Phase 3) ---
        # Nudge the procedure NOTE's embedding toward the query if it
        # passed, away if it failed.  Reuses the chat-loop query embedding
        # already computed for note drift.  No new drift code — just a
        # new caller.  See embedding_drift.py.
        if user_message:
            try:
                q_emb = await loop.run_in_executor(
                    None, svc.vault_indexer._get_embedding, user_message)
                helpful = result.overall_passed
                svc.embedding_drift.record_feedback(
                    str(proc_file), q_emb, helpful=helpful)
                session_logger.log("procedure_drift_feedback", {
                    "procedure": proc_name,
                    "helpful": helpful,
                    "failed_step": result.failed_step,
                })
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log("procedure_drift_feedback_failed",
                                    {"error": str(e)})

        # Log the full procedure result so I can diagnose failures from
        # the session log without guessing. This is the tool whose result
        # determines loop behavior — execute_procedure returning 0 steps
        # was the root cause of the 60-round loop.
        session_logger.log("procedure_result_full", {
            "procedure": proc_name,
            "overall_passed": result.overall_passed,
            "failed_step": result.failed_step,
            "steps_executed": len(result.steps),
            "final_output_len": len(result.final_output),
            "final_output_preview": result.final_output[:500],
            "step_details": [
                {"step": sr.step_number, "type": sr.step_type,
                 "passed": sr.passed,
                 "error": sr.error or sr.validation_error}
                for sr in result.steps
            ],
        })

        _return_dict = {
            "procedure": proc_name,
            "overall_passed": result.overall_passed,
            "failed_step": result.failed_step,
            "steps_executed": len(result.steps),
            "final_output": result.final_output[:4000],
            "child_procedures": result.child_procedures,
            # Surface the trust level so the model weighs the output
            # accordingly (an experimental procedure's result is provisional).
            "caution": ("experimental — unproven procedure; verify the "
                        "output before relying on it" if _proc_caution else ""),
            "step_details": [
                {"step": sr.step_number, "type": sr.step_type,
                 "passed": sr.passed,
                 "error": sr.error or sr.validation_error}
                for sr in result.steps
            ],
        }
        # When the procedure compiled 0 steps, add a diagnosis so the
        # model knows WHY and can fix it. Without this, the model sees
        # "0 steps" and has no way to know the step format was wrong.
        if len(result.steps) == 0:
            _return_dict["diagnosis"] = (
                "PROCEDURE COMPILED 0 STEPS. The procedure compiler "
                "(procedure_compiler.py) only parses numbered list steps "
                "(1. ...) inside a ## Steps section. It does NOT see "
                "### Step N: headers. If your procedure uses ### headers, "
                "rewrite them as '1. ```python ... ```' numbered steps. "
                "See procedure_compiler.py _parse_steps for the format."
            )
        return _return_dict

    # --- Textbook page reader (index-only paradigm) --- #
    # The LLM calls this to read one page of an ingested textbook PDF. The
    # page is rendered to an image and sent to a vision-capable model so
    # equations/figures come through exactly as printed. Falls back to the
    # text layer (with a caveat) if the model can't see images. The result
    # carries provenance so the LLM can cite it in notes.
    #
    # Client selection: prefer the DEDICATED vision client (a separate
    # model the user configured just for page-reading, e.g. a vision model
    # on a different backend while their chat model stays text-only/fast).
    # Fall back to the synthesis client so a vision-capable chat model still
    # works without a separate vision config.
    if tool_name == "textbook_read_page":
        from custom_tools.textbook_read_page import run as _read_page
        page_client = svc.vision_client if svc.vision_client is not None else svc.ollama_client
        # Inject the active page-reading client so the tool can probe vision
        # support and call it for the page read.
        result = await loop.run_in_executor(
            None, lambda: _read_page(args, llm_client=page_client))
        return result

    # --- Web source re-reader (index-only paradigm for web research) --- #
    # The LLM calls this to re-read a source the research engine archived in
    # learningMaterial/web/. Returns the page's article text + provenance to
    # the saved file, so the LLM can verify/quote without re-scraping.
    if tool_name == "web_read_source":
        from custom_tools.web_read_source import run as _read_web
        result = await loop.run_in_executor(None, lambda: _read_web(args))
        return result

    if tool_name == "set_goal":
        # The LLM owns goal management. No heuristic — the agent decides when
        # to set, update, or clear its goal. This is the only path to
        # GOALS.md from the chat loop. See the set_goal tool schema for the
        # contract. Never raises; a failure returns an error dict, the chat
        # loop continues.
        goal = (args.get("goal") or "").strip()
        next_step = (args.get("next_step") or "(awaiting next request)").strip()
        steps = args.get("steps") or None
        context = args.get("context") or None
        if not goal or goal.lower() in ("clear", "none", ""):
            new_text = svc.identity.update_goals(
                goal="(no active goal)",
                steps=None,
                next_step=next_step or "(awaiting next request)")
            session_logger.log("goals_cleared_by_agent", {})
            return {"status": "cleared", "goals_md": new_text[:200]}
        new_text = svc.identity.update_goals(
            goal=goal[:500], steps=steps, next_step=next_step, context=context)
        session_logger.log("goals_set_by_agent", {"goal": goal[:100]})
        return {"status": "set", "goal": goal[:200],
                "goals_md_chars": len(new_text)}

    # --- Working memory (the Copilot/Claude Code TodoList pattern) ------ #
    # The model writes a structured task list via plan_task and updates it
    # via update_task. The harness re-injects the list into the system
    # prompt every round (see handle_chat). This is how the agent stays on
    # track instead of losing the plot to compaction.
    if tool_name == "plan_task":
        session_logger.log("plan_task_branch_enter", {"t_ms": loop.time() * 1000})
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            wm = TaskList()
            websocket.working_memory = wm
        goal = (args.get("goal") or "").strip()
        steps = args.get("steps") or []
        if not goal or not steps:
            return {"error": "plan_task requires 'goal' and 'steps'"}
        snap = wm.set_plan(goal=goal, items=[s for s in steps if s.strip()])
        session_logger.log("plan_task_set", {
            "goal": goal[:100], "steps": len(steps)})
        session_logger.log("plan_task_branch_exit", {"t_ms": loop.time() * 1000})
        return snap

    if tool_name == "update_task":
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            return {"error": "no active plan"}
        action = args.get("action", "update")
        if action == "add":
            content = (args.get("content") or "").strip()
            if not content:
                return {"error": "action='add' requires 'content'"}
            snap = wm.add_task(
                content=content,
                status=args.get("status", "pending"),
                notes=args.get("notes", ""))
            session_logger.log("plan_task_added", {"content": content[:80]})
            return snap
        task_id = args.get("task_id") or ""
        snap = wm.update_task(
            task_id=task_id,
            status=args.get("status", ""),
            notes=args.get("notes", ""))
        session_logger.log("plan_task_updated", {
            "task_id": task_id, "status": args.get("status", "")})
        return snap

    # --- Custom (agent-authored) tools --- #
    if svc.self_improver.has_tool(tool_name):
        result = await loop.run_in_executor(None, lambda: svc.self_improver.execute_custom_tool(
            tool_name, args))
        # Post-ingest weaving: tie newly-ingested textbook notes into the
        # existing vault so the content is actually usable (not inert islands).
        # Runs IN THE BACKGROUND so the tool returns immediately — the agent
        # (and the user) aren't blocked for minutes while 100+ notes get
        # indexed + linked + A-MEM evolved. Progress is pushed to the UI via
        # websocket so the user sees "linking 47/129…" instead of a freeze.
        # Only fires for textbook_ingest; cheap no-op otherwise.
        if tool_name == "textbook_ingest" and isinstance(result, dict):
            note_count = len(result.get("notes_created", []) +
                             result.get("notes_updated", []))
            if note_count > 0:
                result["weaving"] = {
                    "status": "background",
                    "notes_to_weave": note_count,
                    "message": (f"Weaving {note_count} notes into the vault "
                                f"in the background (indexing + linking + "
                                f"evolving neighbors)..."),
                }
                # Fire-and-forget: run the weaving in a background thread so
                # the agent gets the result now and can keep working/talking.
                # Progress events are sent to the websocket from the thread.
                async def _run_weave_bg():
                    try:
                        await weave_textbook_notes(svc,
                            result, websocket=websocket,
                            session_logger=session_logger)
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log("textbook_weave_bg_failed",
                                           {"error": str(e)})
                        # Surface the crash to the user so they know the
                        # notes won't be linked/connected. Without this
                        # the activity line freezes on "linking 47/129…"
                        # forever and the user has no idea it failed.
                        await notify_problem(svc, websocket, e,
                            context={"stage": "weaving textbook notes"},
                            user_message=(
                                "Something went wrong while linking your "
                                "textbook notes into the vault. The notes "
                                "are saved, but they won't be connected to "
                                "other notes until this is fixed."),
                            remedy_hint=(
                                "Try restarting VaultBot. If it keeps "
                                "happening, use Copy for support."))
                asyncio.create_task(_run_weave_bg())
        return result

    return {"error": f"unknown tool: {tool_name}"}
