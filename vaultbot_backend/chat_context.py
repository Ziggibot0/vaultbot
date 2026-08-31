"""Context management helpers for the agentic chat loop.

Extracted from ``chat_handler.py`` -- these functions operate on the
``conversation`` list: deduplication of seen search results, proactive
tool-result aging, hard token-cap enforcement, code-read digesting, and
provider-safe message projection.

All are pure functions (no ``Services`` dependency, no I/O, no WebSocket
access). They take a conversation list and return a new/modified list.
"""

from __future__ import annotations

import json
import os
from typing import Any

from config import TUNABLES
from session_logger import SessionLoggerProtocol

# ── Provider-safe message projection ────────────────────────────────────
#
# The internal conversation list carries fields the model should never see:
# ``thinking`` (prior reasoning), ``timestamp``, ``digested``,
# ``original_chars``, etc. These are bookkeeping fields for the frontend
# UI, the session log, and the loop logic. Sending them to the LLM
# provider corrupts the token sequence in two ways:
#
#   1. **KV cache invalidation**: the token sequence changes every round
#      (new ``thinking`` content appended), so Ollama can't reuse the
#      cached prefix and re-evaluates the entire prompt from scratch.
#      This is the "almost completely restart inference instead of
#      caching" symptom — the user waits 2+ minutes for a simple answer.
#
#   2. **Generation corruption**: non-spec fields like ``thinking`` get
#      passed through the provider's chat template as raw text, polluting
#      the model's attention with its own prior reasoning. This causes
#      degenerate text repetition ("Let me sync first...Let me sync
#      first...Let me sync first...").
#
# The fix is a single universal projection: before sending to ANY
# provider, strip every field that isn't part of the OpenAI message spec.
# This replaces the per-model-family ``sanitize_tool_history`` heuristic
# (which only fired for glm models) with a clean, model-agnostic approach.
#
# See: session eb8143f7 (qwen3.6:27b degenerate looping + 2-min latency).

# Fields that are part of the OpenAI chat-completions message spec and
# safe to send to any provider. Everything else is internal bookkeeping.
_SPEC_FIELDS: frozenset[str] = frozenset(
    {
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
        "name",
    }
)

# Fields that should be stripped from assistant messages when the model
# uses native tool protocol (not the glm-flattened system-message path).
# ``thinking`` is the main offender — it's reasoning text the model already
# produced and should never see again in its input context.
_STRIP_FROM_ASSISTANT: frozenset[str] = frozenset(
    {
        "thinking",
        "timestamp",
        "digested",
        "original_chars",
    }
)


def project_for_provider(
    conversation: list[dict[str, Any]],
    *,
    flatten_tool_calls: bool = False,
) -> list[dict[str, Any]]:
    """Return a provider-safe copy of the conversation.

    Strips all internal bookkeeping fields (``thinking``, ``timestamp``,
    ``digested``, etc.) that are not part of the OpenAI message spec.
    This ensures the token sequence is stable across rounds so the
    provider's KV cache can hit on the prefix, and prevents the model
    from seeing its own prior reasoning as regular text.

    Args:
        conversation: The internal conversation list (mutated by the loop).
        flatten_tool_calls: When True, also run the glm-specific tool-call
            flattening (convert ``tool`` role → ``system`` role, strip
            ``tool_calls`` from assistant messages). This is the old
            ``sanitize_tool_history`` behavior, needed only for glm-via-
            Ollama which returns empty when it sees tool_calls/tool-role
            messages. When False (default), tool calls and tool results
            pass through with native protocol — only non-spec fields are
            stripped.

    Returns:
        A new list of new dicts. The original conversation is not modified.
    """
    if flatten_tool_calls:
        return _project_flattened(conversation)
    return _project_native(conversation)


def _project_native(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project for native tool protocol (all providers except glm-via-Ollama).

    Strips non-spec fields but keeps ``tool_calls`` and ``tool`` role
    messages intact so the model can reference prior tool interactions
    via the standard protocol.
    """
    projected = []
    for msg in conversation:
        role = msg.get("role", "")
        new_msg: dict[str, Any] = {"role": role}
        # content — always include (even if empty string)
        new_msg["content"] = msg.get("content", "")
        # tool_calls — keep for assistant messages (native protocol)
        if msg.get("tool_calls"):
            new_msg["tool_calls"] = msg["tool_calls"]
        # tool_call_id — keep for tool-role messages
        if msg.get("tool_call_id"):
            new_msg["tool_call_id"] = msg["tool_call_id"]
        # name — keep (used by some providers for tool naming)
        if msg.get("name"):
            new_msg["name"] = msg["name"]
        projected.append(new_msg)
    return projected


def _project_flattened(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project for glm-via-Ollama (flatten tool calls to system messages).

    This is the old ``sanitize_tool_history`` behavior: convert ``tool``
    role messages to ``system`` role with combined call+result info, and
    strip ``tool_calls`` from assistant messages. Also strips ``thinking``
    and other non-spec fields.

    Kept as a separate path because glm-5.2:cloud via Ollama returns
    completely empty when it sees tool_calls/tool-role messages — this is
    a protocol bug specific to that model, not a general heuristic.
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

    projected = []
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
            except Exception:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
                args_str = str(args_raw)[:300]
            # Build the combined system message
            result_text = tool_content
            # Read tools (vault_read_note, code_read) return the ENTIRE file:
            # never truncate them here, even when sanitizing for Ollama, so
            # the model gets the whole file in one read. Other tool results
            # are capped to keep the sanitized history bounded.
            if (
                tool_name not in ("code_read", "vault_read_note")
                and len(result_text) > 2000
            ):
                result_text = result_text[:2000] + "...[truncated]"
            system_content = (
                f"[Tool call: {tool_name}({args_str}) returned: {result_text}]"
            )
            # Merge multiple tool results into one system message if the
            # previous projected message is also a tool-result system msg.
            if (
                projected
                and projected[-1].get("role") == "system"
                and str(projected[-1].get("content", "")).startswith("[Tool call:")
            ):
                projected[-1]["content"] += "\n" + system_content
            else:
                projected.append({"role": "system", "content": system_content})
            continue
        # Assistant messages with tool_calls → strip tool_calls, keep content
        if role == "assistant" and msg.get("tool_calls"):
            content = msg.get("content", "") or ""
            # Keep the model's actual text. If empty (tool-only round), use
            # empty string -- the system message with the tool result provides
            # the context. Using a placeholder like "(working...)" or "." caused
            # the model to echo it back in subsequent rounds.
            projected.append({"role": "assistant", "content": content})
            continue
        # All other messages: keep only spec fields
        new_msg: dict[str, Any] = {"role": role}
        new_msg["content"] = msg.get("content", "")
        projected.append(new_msg)
    return projected


# ---------------------------------------------------------------------------
# Ollama tool-call history sanitization (backward compat)
# ---------------------------------------------------------------------------


def sanitize_tool_history(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-compat alias for :func:`project_for_provider` with flattening.

    Kept so existing import sites (``from chat_context import
    sanitize_tool_history``) don't break. New code should call
    ``project_for_provider`` directly.
    """
    return project_for_provider(conversation, flatten_tool_calls=True)


def _leading_system_count(conversation: list[dict[str, Any]]) -> int:
    """Count how many leading system messages the conversation has.

    The prompt-caching structure (2026-08-15) splits the prefix into up to
    3 separate system messages (stable prompt, vault context, wm block).
    All pruning/aging functions must skip ALL of them -- touching any
    system message in the prefix invalidates the provider's prompt cache.

    Returns the count of consecutive ``role: system`` messages at the start
    of the conversation. Stops at the first non-system message.
    """
    count = 0
    for msg in conversation:
        if isinstance(msg, dict) and msg.get("role") == "system":
            count += 1
        else:
            break
    return count


# ---------------------------------------------------------------------------
# Seen-content deduplication (breaks the "search anxiety" loop)
# ---------------------------------------------------------------------------
# When the model calls vault_search repeatedly with rephrased queries, it gets
# back the same files over and over. This function filters out results the
# model has already seen this turn (via a prior vault_search or code_read),
# and returns the omitted list so the tool result can tell the model exactly
# which files were dropped and why.


def dedup_seen_results(
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
      - source "code_read" with lines=(s,e) (partial -- annotated with
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
            # Never seen -- keep it as-is.
            annotated.append(r)
            continue
        # Already seen -- annotate it but KEEP it so the model can see
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
# Tool-result write detection
# ---------------------------------------------------------------------------


def tool_actually_wrote(tool_name: str, result: Any) -> bool:
    """Determine whether a tool call actually changed something.

    This is the core fix for the read-loop detector: a "write" tool that
    failed (execute_procedure returning 0 steps, vault_safe_write rejected,
    code_run with an error) is NOT a successful write -- it's a failed read.
    Counting it as a write resets the read-loop detector and lets the model
    loop forever calling a broken tool.

    Returns True only when the tool produced a real, successful change.
    """
    if not isinstance(result, dict):
        return False
    if tool_name == "execute_procedure":
        return result.get("steps_executed", 0) > 0
    if tool_name in ("vault_safe_write", "safe_write", "js_safe_write"):
        # vault_safe_write (custom tool) returns status="written" on success,
        # "blocked" or "dry_run" otherwise. safe_write returns "ok".
        # Accept both "ok" and "written" as success, and require no error
        # and bytes_written > 0 (a 0-byte write is not a real write).
        _st = result.get("status", "")
        return (
            _st in ("ok", "written")
            and not result.get("error")
            and result.get("bytes_written", 1) > 0
        )
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
    # Unknown write tool -- be conservative: treat as write only if no error.
    return not result.get("error")


def round_tool_outcome(tool_name: str, result: Any) -> str:
    """Summarize the ACTUAL outcome of a tool call for the findings ledger.

    Reads the tool result payload (not the write-failure heuristic) so the
    ledger tells the model the truth about what happened (issue #386):
    a failed search/read is ``failed(<error>)``, a procedure-suggestion
    nudge is ``suggested``, and anything else is ``ok``.

    Pure function; unit-tested in tests/test_findings_ledger.py.
    """
    if not isinstance(result, dict):
        return "ok"
    if "procedure_suggestion" in result or "proceed_keyword" in result:
        return "suggested"
    err = result.get("error")
    if err:
        return f"failed({str(err)[:60]})"
    return "ok"


# ---------------------------------------------------------------------------
# Proactive tool-result aging -- keep the model focused on recent results
# ---------------------------------------------------------------------------
# The token cap (enforce_token_cap) only fires when total tokens exceed
# ~60K. Below that, old tool results accumulate full-size across rounds and
# the model re-processes them every round -- bloating the prompt and
# distracting the model from the current task. This function runs EVERY
# round (before the token cap check) and stubs tool results older than N
# rounds back to a 1-line summary, regardless of total token count.
#
# Why age-based, not size-based: the model already processed those results
# in prior rounds. It doesn't need the full payload again -- just a reminder
# of what it did. The findings ledger (in the system prompt) already tracks
# round-level outcomes; this complements it by shrinking the heavy payloads
# that sit in conversation history. Together they keep the model aware of
# its progress without forcing it to re-read stale data.
#
# Never breaks tool_call/tool_result pairing: stubs CONTENT only, keeps the
# message + tool_call_id intact. Never touches the most recent N rounds so
# the model can still act on results it just received.


def age_old_tool_results(
    conversation: list[dict[str, Any]],
    session_logger: SessionLoggerProtocol | None = None,
    round_idx: int = 0,
) -> list[dict[str, Any]]:
    """Stub old tool results to a 1-line summary, independent of token cap.

    Runs every round. Tool results older than ``tool_age_rounds_back``
    rounds (counted by assistant-with-tool_calls messages) get their
    ``content`` replaced with a compact stub. The most recent N rounds are
    kept verbatim so the model can act on results it just received.

    Returns a new list; the caller's list is not mutated.
    """
    _rounds_back = int(
        os.getenv("VAULTBOT_TOOL_AGE_ROUNDS_BACK", str(TUNABLES.tool_age_rounds_back))
    )
    if _rounds_back <= 0:
        return conversation  # disabled

    _min_chars = int(
        os.getenv("VAULTBOT_TOOL_AGE_MIN_CHARS", str(TUNABLES.tool_age_min_chars))
    )
    _protect_reads = (
        os.getenv(
            "VAULTBOT_TOOL_AGE_PROTECT_FILE_READS",
            "1" if TUNABLES.tool_age_protect_read_tools else "0",
        )
        == "1"
    )

    # Build the list of assistant-message indices that carry tool_calls.
    # Each such message marks the start of a tool round. We count rounds
    # from the END of the conversation so "rounds back" is relative to now.
    tool_round_starts: list[int] = []
    for i, m in enumerate(conversation):
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
            tool_round_starts.append(i)

    if len(tool_round_starts) <= _rounds_back:
        return conversation  # not enough rounds to age anything

    # The cutoff index: tool results at or after this conversation index
    # are within the protected recent window and stay verbatim. Tool
    # results BEFORE it are old enough to stub.
    _cutoff_idx = tool_round_starts[-_rounds_back]

    # Work on a shallow copy so we don't mutate the caller's list.
    conv = [dict(m) if isinstance(m, dict) else m for m in conversation]
    _stubbed = 0
    _skip = _leading_system_count(conv)  # skip all leading system messages
    for i in range(_skip, _cutoff_idx):
        m = conv[i]
        if not (isinstance(m, dict) and m.get("role") == "tool"):
            continue
        _content = m.get("content", "")
        if not (isinstance(_content, str) and len(_content) > _min_chars):
            continue
        _tool_name = m.get("tool_name", "tool")
        if _protect_reads and _tool_name in ("code_read", "vault_read_note"):
            continue
        # Build a compact stub: tool name + first line of the result so the
        # model knows what it called and the gist of what it got back.
        _preview = _content[:120].replace("\n", " ").strip()
        conv[i] = dict(m)
        conv[i]["content"] = (
            f"[Old tool output from {_tool_name} (rounds ago) -- "
            f"cleared to keep context focused on the current task. "
            f"Preview: {_preview}… Re-call the tool if you need the "
            f"raw data again.]"
        )
        _stubbed += 1

    if _stubbed and session_logger:
        session_logger.log(
            "tool_results_aged",
            {
                "round": round_idx,
                "stubbed_count": _stubbed,
                "rounds_back": _rounds_back,
                "cutoff_idx": _cutoff_idx,
            },
        )
    return conv


# ---------------------------------------------------------------------------
# Hard token cap -- the GUARANTEED ceiling on what's sent to the big LLM
# ---------------------------------------------------------------------------
# Every other budgeting mechanism (context_budgeter, preflight compression,
# truncate_tool_result) is advisory and piecemeal. This function is the
# enforcement layer: right before the LLM call, it estimates the total
# token count of the entire conversation and, if it exceeds the cap,
# prunes from the oldest/heaviest content first -- without ever breaking
# tool_call/tool_result pairs.
#
# Pruning strategy (in order, stop when under cap):
#   1. Stub old tool-result CONTENT (not the message, not the pairing).
#      Tool results from 2+ rounds ago that are still large get their
#      content replaced with a 1-line stub. The tool_call_id and message
#      index stay intact -- the provider sees a valid pair, just with
#      shrunk payload. The model already processed those results.
#   2. If still over, stub ALL remaining old tool results (even small
#      ones) outside the protected tail.
#   3. If still over, drop old non-system, non-tool messages from the
#      middle of conversation_history (keeping head + tail).
#
# The cap is ~60K tokens by default (TUNABLES.max_send_tokens, overridable
# via VAULTBOT_MAX_SEND_TOKENS). This is the fix for the user's "2000 t/s
# but still slow" symptom: the model was getting 100K+ tokens of prompt
# every round because nothing else bounded the total.


def estimate_conv_tokens(conversation: list[dict[str, Any]]) -> int:
    """Rough token estimate for the whole conversation (~4 chars/token)."""
    total_chars = 0
    for m in conversation:
        if not isinstance(m, dict):
            continue
        content = m.get("content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        total_chars += len(content)
        # tool_calls also consume tokens
        tcs = m.get("tool_calls")
        if tcs:
            total_chars += len(json.dumps(tcs, default=str))
    return max(1, total_chars // TUNABLES.chars_per_token)


def enforce_token_cap(
    conversation: list[dict[str, Any]],
    session_logger: SessionLoggerProtocol | None = None,
    round_idx: int = 0,
) -> list[dict[str, Any]]:
    """Guarantee the conversation fits within the hard token cap.

    Mutates a COPY of conversation in place (the caller's list is not
    affected -- we return the trimmed copy). Never removes messages or
    breaks tool_call/tool_result pairing; only shrinks content of old
    tool results and, as a last resort, drops old middle messages.

    Returns the (possibly trimmed) conversation list.
    """
    _cap = int(os.getenv("VAULTBOT_MAX_SEND_TOKENS", str(TUNABLES.max_send_tokens)))
    if _cap <= 0:
        return conversation  # disabled

    _est = estimate_conv_tokens(conversation)
    if _est <= _cap:
        return conversation  # already under -- no action

    # Work on a shallow copy so we don't mutate the caller's list.
    conv = [dict(m) if isinstance(m, dict) else m for m in conversation]

    _protect_last = int(os.getenv("VAULTBOT_CAP_PROTECT_LAST_N", "8"))
    _stub_min_chars = int(os.getenv("VAULTBOT_CAP_STUB_MIN_CHARS", "2000"))

    # Skip all leading system messages (prompt-caching structure: up to 3
    # system messages -- stable prompt, vault context, wm block). Touching
    # any of them would invalidate the provider's prefix cache.
    _skip = _leading_system_count(conv)

    # ── Phase 1: Stub large old tool results (2+ rounds back) ──
    # Read tools (code_read, vault_read_note) are EXEMPT -- the user wants
    # the model to read the whole file, and the read_result_cap already
    # bounds the initial size. Stubbing a read result the model is still
    # reasoning over forces a re-read, wasting a round-trip.
    _protect_read_tools = os.getenv("VAULTBOT_CAP_PROTECT_FILE_READS", "1") == "1"
    _pruned = 0
    _cutoff = max(_skip, len(conv) - _protect_last)
    for i in range(_skip, _cutoff):
        m = conv[i]
        if not (
            isinstance(m, dict)
            and m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and len(m["content"]) > _stub_min_chars
        ):
            continue
        if _protect_read_tools and m.get("tool_name") in (
            "code_read",
            "vault_read_note",
        ):
            continue
        conv[i] = dict(m)
        conv[i]["content"] = (
            "[Old tool output cleared to stay within token cap. "
            "Re-call the tool if you need the raw data again.]"
        )
        _pruned += 1

    _est = estimate_conv_tokens(conv)
    if _est <= _cap:
        if _pruned and session_logger:
            session_logger.log(
                "token_cap_pruned",
                {
                    "round": round_idx,
                    "pruned_count": _pruned,
                    "est_tokens_before": _est,
                    "est_tokens_after": _est,
                    "phase": "large_old_tools",
                    "cap": _cap,
                },
            )
        return conv

    # ── Phase 2: Stub ALL old tool results (even small ones) ──
    # Read tools are still exempt here -- see Phase 1 comment.
    _pruned2 = 0
    for i in range(_skip, _cutoff):
        m = conv[i]
        if not (
            isinstance(m, dict)
            and m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and len(m["content"]) > 200
        ):
            continue
        if _protect_read_tools and m.get("tool_name") in (
            "code_read",
            "vault_read_note",
        ):
            continue
        conv[i] = dict(m)
        conv[i]["content"] = "[Old tool output cleared to stay within token cap.]"
        _pruned2 += 1

    _est = estimate_conv_tokens(conv)
    if _est <= _cap:
        if session_logger:
            session_logger.log(
                "token_cap_pruned",
                {
                    "round": round_idx,
                    "pruned_count": _pruned + _pruned2,
                    "est_tokens_after": _est,
                    "phase": "all_old_tools",
                    "cap": _cap,
                },
            )
        return conv

    # ── Phase 2b: Truncate old tool_call ARGUMENTS in assistant messages ──
    # tool_calls live inside assistant messages as JSON. The 'thought' tool
    # is the worst offender: the model writes 2K-6K-char reasoning dumps as
    # its argument, and those accumulate forever because phases 1-2 only
    # stub tool RESULTS, not tool CALLS. This phase truncates old tool_call
    # arguments to a short stub, preserving the call structure (name, id,
    # tool_call_id) so pairings stay valid -- only the heavy argument payload
    # is replaced with a 1-line note.
    _pruned_calls = 0
    _call_stub = "[Old tool call arguments cleared to stay within token cap.]"
    for i in range(_skip, _cutoff):
        m = conv[i]
        if not (
            isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
        ):
            continue
        tcs = m.get("tool_calls")
        if not isinstance(tcs, list):
            continue
        _modified = False
        for tc in tcs:
            if not (isinstance(tc, dict) and isinstance(tc.get("function"), dict)):
                continue
            _fn = tc["function"]
            _args = _fn.get("arguments", "")
            if isinstance(_args, str) and len(_args) > 300:
                _fn["arguments"] = _call_stub
                _pruned_calls += 1
                _modified = True
        if _modified:
            # Re-assign the modified list so estimate_conv_tokens sees the change
            m = dict(m)
            m["tool_calls"] = tcs
            conv[i] = m

    _est = estimate_conv_tokens(conv)
    if _est <= _cap:
        if session_logger:
            session_logger.log(
                "token_cap_pruned",
                {
                    "round": round_idx,
                    "pruned_count": _pruned + _pruned2,
                    "pruned_calls": _pruned_calls,
                    "est_tokens_after": _est,
                    "phase": "old_tool_call_args",
                    "cap": _cap,
                },
            )
        return conv

    # ── Phase 2c: Unprotect read tool results (last-resort before drop) ──
    # code_read and vault_read_note results were exempt in phases 1-2. If
    # we're still over cap, stub them too -- keeping a 100KB old file dump in
    # context is worse than forcing a re-read later.
    _pruned3 = 0
    for i in range(_skip, _cutoff):
        m = conv[i]
        if not (
            isinstance(m, dict)
            and m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and len(m["content"]) > 200
        ):
            continue
        # No read-tool exemption here -- stub everything.
        conv[i] = dict(m)
        conv[i]["content"] = "[Old tool output cleared to stay within token cap.]"
        _pruned3 += 1

    _est = estimate_conv_tokens(conv)
    if _est <= _cap:
        if session_logger:
            session_logger.log(
                "token_cap_pruned",
                {
                    "round": round_idx,
                    "pruned_count": _pruned + _pruned2 + _pruned3,
                    "pruned_calls": _pruned_calls,
                    "est_tokens_after": _est,
                    "phase": "unprotected_reads",
                    "cap": _cap,
                },
            )
        return conv

    # ── Phase 3: Drop old middle messages (keep head + tail) ──
    # Head = all leading system messages + first 2 history msgs.
    # Tail = last _protect_last messages.
    # Middle = old user/assistant turns that aren't tool messages.
    _dropped = 0
    _head_keep = _skip + 2  # system messages + first 2 conversation msgs
    _tail_start = max(_head_keep, len(conv) - _protect_last)
    new_conv = conv[:_head_keep]
    for i in range(_head_keep, _tail_start):
        m = conv[i]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            # Skip assistant messages that have tool_calls (would orphan tool results)
            if m.get("tool_calls"):
                new_conv.append(m)
            else:
                _dropped += 1
                continue
        else:
            new_conv.append(m)
    new_conv.extend(conv[_tail_start:])

    _est = estimate_conv_tokens(new_conv)
    if session_logger:
        session_logger.log(
            "token_cap_pruned",
            {
                "round": round_idx,
                "pruned_tools": _pruned + _pruned2,
                "dropped_msgs": _dropped,
                "est_tokens_after": _est,
                "phase": "drop_middle",
                "cap": _cap,
                "conv_before": len(conversation),
                "conv_after": len(new_conv),
            },
        )
    return new_conv


# ---------------------------------------------------------------------------
# Code-read digesting (protects thinking-model reasoning budget)
# ---------------------------------------------------------------------------


def digest_code_read(result: dict[str, Any]) -> dict[str, Any]:
    """Compress a code_read tool result into a compact structural digest.

    A thinking model (nemotron, o1, qwq, etc.) handed a ~35KB raw file dump
    tends to ECHO it char-by-char inside its reasoning field, burning its
    whole output budget before it ever writes the answer -- the "cut out"
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
    # "I read my own code" -- what this file depends on).
    imports = [
        line
        for line in lines
        if line.strip().startswith(
            ("import ", "from ", "require(", "#include", "using ")
        )
    ]
    # Pull top-level definition names (def/class/function) for structure.
    import re as _re

    defs = []
    for line in lines:
        m = _re.match(
            r"\s*(?:def|class|function|const|async def)\s+([A-Za-z_][\w]*)", line
        )
        if m:
            defs.append(m.group(1))

    digest_lines = [
        "[DIGESTED code_read -- full body omitted to protect your reasoning budget]",
        f"file: {file_path}",
        f"size: {total} total lines; you read lines {start}-{end} "
        f"({len(lines)} lines).",
    ]
    if imports:
        digest_lines.append(
            f"imports ({len(imports)}): "
            + "; ".join(import_.strip()[:70] for import_ in imports[:12])
        )
    if defs:
        digest_lines.append(f"definitions ({len(defs)}): " + ", ".join(defs[:25]))
    digest_lines.append(
        "The raw body is NOT included. If you need a specific section, call "
        "code_read again with a tight start_line/end_line around that symbol "
        "instead of re-reading the whole file."
    )

    new = dict(result)
    new["content"] = "\n".join(digest_lines)
    new["digested"] = True
    new["original_chars"] = len(content)
    return new
