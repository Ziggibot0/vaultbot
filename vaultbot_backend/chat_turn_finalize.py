"""Post-loop cleanup, grounding enforcement, answer delivery.

Extracted from ``chat_handler.py`` — ``finalize_turn`` runs AFTER the
agentic loop completes. It verifies the answer is grounded in vault
notes (wikilink existence check), tracks token cost, emits the
``answer_done`` event, clears the chat-loop checkpoint, reports context
usage, and logs the turn summary. Returns the (possibly modified) final
answer with grounding caution appended.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path

from chat_loop_state import TurnState
from config import TUNABLES
from services import Services

# Tools whose output is a live, authoritative fact source (not vault
# retrieval, not planning/self-edit bookkeeping). When a turn used one of
# these, the answer is grounded in the tool's output — not model weights —
# so the grounding gate must not false-alarm (issue #132).
LIVE_FACT_TOOLS: frozenset[str] = frozenset(
    {
        "google_workspace",
        "calendar_list",
        "calendar_events",
        "code_read",
        "code_run",
        "github_issues",
        "web_read_source",
        "vault_research",
        "machine_spec",
        "ollama_model_search",
    }
)


def is_tool_sourced(turn_tool_history: list | None) -> bool:
    """True if the turn used a live fact-source tool (issue #132).

    Pure function so it can be unit-tested directly. ``turn_tool_history``
    is the list of ``{"tool": name, ...}`` dicts recorded per round.
    """
    if not turn_tool_history:
        return False
    tools = {e.get("tool", "") for e in turn_tool_history if isinstance(e, dict)}
    return bool(tools & LIVE_FACT_TOOLS)


def score_code_grounding(turn_tool_history: list | None) -> dict:
    """Score how doc-proven this turn's code edits were.

    The code analogue of the chat closed-set citation gate. A ``safe_write``
    that imports external modules must carry a ``doc_source`` (official-docs
    URL) or it is rejected by the gate — so a turn that wrote code WITHOUT
    any doc-proven edit is a red flag (the model may have bypassed the gate
    via code_write / code_run, which is forbidden).

    Returns a dict with:
      - safe_writes: number of safe_write calls this turn
      - doc_proven: number of those that carried a doc_source
      - unproven: safe_writes - doc_proven
      - bypassed: True if the turn wrote files via code_write/code_run
        (the forbidden path) without any doc-proven safe_write.
    """
    if not turn_tool_history:
        return {"safe_writes": 0, "doc_proven": 0, "unproven": 0, "bypassed": False}
    safe_writes = 0
    doc_proven = 0
    bypassed = False
    for e in turn_tool_history:
        if not isinstance(e, dict):
            continue
        tool = e.get("tool", "")
        if tool == "safe_write":
            safe_writes += 1
            if e.get("doc_source"):
                doc_proven += 1
        elif tool in ("code_write", "code_run"):
            # code_write / code_run are the forbidden file-modification
            # path (no doc gate). Flag as a potential bypass.
            bypassed = True
    return {
        "safe_writes": safe_writes,
        "doc_proven": doc_proven,
        "unproven": safe_writes - doc_proven,
        "bypassed": bypassed,
    }


async def finalize_turn(
    svc: Services,
    websocket,
    session_logger,
    loop,
    final_answer: str,
    thinking_text: str,
    total_chunks: int,
    round_idx: int,
    t0: float,
    _turn_token_totals: dict,
    _model_conversation: list,
    conversation: list,
    partial_path: Path,
    _cp,
    st: TurnState | None = None,
) -> str:
    """Post-loop cleanup, grounding enforcement, answer delivery.

    Returns the (possibly modified) final_answer with grounding caution
    appended.
    """
    session_logger.log(
        "llm_generate",
        {
            "model": svc.ollama_client.llm_model,
            "stream": True,
            "total_chunks": total_chunks,
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
            "duration_ms": (loop.time() - t0) * 1000,
        },
    )

    # finalize_turn is called from handle_chat with a live TurnState, but keep
    # a defensive fallback for type-safety and tests that may call it directly.
    if st is None:
        st = TurnState()

    # --- Grounding enforcement: closed-set citation gate ----------------
    # The big LLM is a synthesis router. Its answer must cite notes from
    # the per-turn allowed-citations set (st._allowed_citations), built
    # from the retrieved vault context. We score the answer against that
    # closed set: every [[wikilink]] must be in the set, and every factual
    # sentence must contain one. If the answer fails (zero citations, or
    # too many ungrounded sentences), we flag it for a retry re-entry into
    # the agentic loop (capped at TUNABLES.max_grounding_retries) with a
    # reprimand. After the retry cap, we ship the answer + a ⚠️ caution so
    # the user is never left with no answer.
    #
    # IDK escape hatch: if the model said "I don't know" (the correct
    # response when the vault has nothing relevant), skip the grounding
    # retry entirely. An IDK answer is not a factual claim — it's an
    # admission of ignorance. Retrying it wastes an LLM round and may
    # force the model to cite irrelevant notes just to pass the gate.
    _allowed = getattr(st, "_allowed_citations", None) or {}
    _score: dict = {}
    _grounding_caution = ""
    _is_idk = False
    _is_temporal = bool(getattr(st, "_is_temporal_question", False))
    _is_coaching = bool(getattr(st, "_is_coaching_turn", False))
    _is_conversational = False
    _graph_lookup: Callable[[str], bool] | None = None
    # Tool-sourced answer detection (issue #132): when the turn's facts
    # came from LIVE tool calls (calendar, code_read, github_issues, etc.)
    # rather than vault retrieval, the answer is grounded in the tool's
    # output — not model weights. The grounding gate only knows about vault
    # notes, so it would false-alarm (0% grounded) on a correct calendar
    # answer. Detect that and suppress the scary "may draw on model
    # weights" warning, replacing it with a neutral "sourced from live
    # tools" note.
    _is_tool_sourced = is_tool_sourced(getattr(st, "_turn_tool_history", None))
    try:

        def _graph_lookup(_wl):
            note = svc.vault_graph.get_note(_wl)
            return bool(isinstance(note, dict) and note.get("file_path"))

    except Exception:  # noqa: BLE001 — best-effort
        _graph_lookup = None
    if final_answer and len(final_answer) > 50:
        try:
            from citation_gate import (
                build_reprimand,
                detect_conversational,
                detect_idk,
                score_grounding,
            )

            # Check IDK BEFORE scoring — skip the grounding retry for
            # admissions of ignorance. We still score (for logging) but
            # don't trigger a retry or append a caution.
            _is_idk = detect_idk(final_answer)
            _is_conversational = detect_conversational(final_answer)
            _score = score_grounding(final_answer, _allowed, _graph_lookup)
            session_logger.log(
                "grounding_check",
                {
                    "total_wikilinks": _score["total_wikilinks"],
                    "allowed_cited": _score["allowed_cited"],
                    "missing_from_set": _score["missing_from_set"],
                    "missing_from_vault": _score["missing_from_vault"],
                    "sentences": _score["sentences"],
                    "ungrounded_sentences": _score["ungrounded_sentences"],
                    "ungrounded_ratio": _score["ungrounded_ratio"],
                    "grounding_score": _score["grounding_score"],
                    "failed": _score["failed"],
                    "allowed_set_size": len(_allowed),
                    "retry_count": getattr(st, "_grounding_retry_count", 0),
                    "is_idk": _is_idk,
                    "is_temporal": _is_temporal,
                    "is_coaching": _is_coaching,
                    "is_conversational": _is_conversational,
                    "is_tool_sourced": _is_tool_sourced,
                },
            )
            if (
                _score["failed"]
                and not _is_idk
                and not _is_temporal
                and not _is_coaching
                and not _is_conversational
                and not _is_tool_sourced
            ):
                # Hard gate: flag for retry if under the cap.
                _retries = getattr(st, "_grounding_retry_count", 0)
                if _retries < TUNABLES.max_grounding_retries:
                    st._grounding_failed = True
                    st._grounding_reprimand = build_reprimand(_score, _allowed)
                    session_logger.log(
                        "grounding_retry_requested",
                        {
                            "retry_count": _retries,
                            "max": TUNABLES.max_grounding_retries,
                        },
                    )
                    return final_answer  # caller re-enters the loop
                else:
                    # Retry cap reached — ship with a visible caution.
                    _grounding_caution = (
                        f"\n\n> ⚠️ **Grounding check**: This answer may be "
                        f"partially ungrounded ({_score['ungrounded_sentences']}/"
                        f"{_score['sentences']} sentences uncited, "
                        f"grounding_score {_score['grounding_score']}). "
                        f"It may draw on model weights rather than your "
                        f"vault. Consider asking me to verify or research "
                        f"this topic."
                    )
                    final_answer += _grounding_caution
            elif _score["failed"] and (
                _is_idk
                or _is_temporal
                or _is_coaching
                or _is_conversational
                or _is_tool_sourced
            ):
                # IDK answer failed grounding (expected — it has no factual
                # claims to cite), a temporal/recency question (grounded
                # in conversation history, not the vault closed set), a
                # coaching/planning turn (issue #277; user intent is
                # life-management guidance, not factual vault synthesis), a
                # in conversation history, not the vault closed set), a
                # short conversational answer (greeting/small-talk with no
                # vault content to ground — issue #334), or a tool-sourced
                # answer (grounded in a live tool call, not vault notes —
                # issue #132). Log and skip — don't retry, don't caution.
                session_logger.log(
                    "grounding_skipped_idk",
                    {
                        "retry_count": getattr(st, "_grounding_retry_count", 0),
                        "is_temporal": _is_temporal,
                        "is_coaching": _is_coaching,
                        "is_conversational": _is_conversational,
                        "is_tool_sourced": _is_tool_sourced,
                    },
                )
            elif (
                _score["grounding_score"] < 0.5
                and _score["total_wikilinks"] > 0
                and not _is_temporal
                and not _is_coaching
                and not _is_conversational
                and not _is_tool_sourced
            ):
                # Some citations but many missing from the set/vault — soft warn.
                _grounding_caution = (
                    f"\n\n> ⚠️ **Grounding check**: Only "
                    f"{_score['allowed_cited']}/{_score['total_wikilinks']} "
                    f"cited notes were in the allowed set. Missing: "
                    f"{', '.join(_score['missing_from_set'][:5])}. "
                    f"This answer may be partially ungrounded."
                )
                final_answer += _grounding_caution
        except Exception as _e:  # noqa: BLE001 — best-effort
            session_logger.log("grounding_check_failed", {"error": str(_e)})

    # --- Code-grounding score (the code analogue of the citation gate) ---
    # Log how doc-proven this turn's code edits were. A turn that wrote
    # code via code_write/code_run (the forbidden, un-gated path) without
    # any doc-proven safe_write is flagged — the model may have bypassed
    # the Prove-Code-Change gate. This is observational (it logs + warns),
    # not a hard gate: the hard gate lives in safe_write itself.
    try:
        _code_score = score_code_grounding(getattr(st, "_turn_tool_history", None))
        if _code_score["safe_writes"] or _code_score["bypassed"]:
            session_logger.log("code_grounding_check", _code_score)
            if _code_score["bypassed"] and _code_score["doc_proven"] == 0:
                session_logger.log(
                    "code_grounding_bypass_warning",
                    {
                        "message": (
                            "This turn modified files via code_write/code_run "
                            "without a doc-proven safe_write. The Prove-Code-"
                            "Change gate was bypassed."
                        )
                    },
                )
    except Exception as _e:  # noqa: BLE001 — best-effort
        session_logger.log("code_grounding_check_failed", {"error": str(_e)})

    # --- Positive provenance surface: trust badge + Sources block --------
    # After grounding enforcement, append a POSITIVE affordance so a scholar
    # can see where the answer came from: a one-line trust badge + a
    # "## Sources" list of clickable [[wikilinks]]. This is the "I can see
    # where the answers are coming from" moment. Only added when the answer
    # actually cites vault notes AND is not an IDK answer (a greeting or
    # "I don't know" gets no block — the citations in an IDK answer are
    # just the irrelevant notes the model was told it could cite, not
    # real provenance for a factual claim).
    if (
        not _is_idk
        and not _is_temporal
        and not _is_coaching
        and not _is_conversational
        and not _is_tool_sourced
    ):
        try:
            from citation_gate import build_sources_block, build_trust_badge

            _badge = build_trust_badge(_score) if _score else ""
            _sources = build_sources_block(final_answer, _allowed)
            if _sources:
                final_answer = f"{final_answer}\n\n{_badge}\n\n{_sources}"
                session_logger.log(
                    "provenance_surface",
                    {"badge": _badge, "sources": len(_sources.splitlines()) - 1},
                )
        except Exception as _e:  # noqa: BLE001 — best-effort
            session_logger.log("provenance_surface_failed", {"error": str(_e)})
    else:
        session_logger.log(
            "provenance_surface_skipped_idk",
            {
                "is_temporal": _is_temporal,
                "is_coaching": _is_coaching,
                "is_conversational": _is_conversational,
                "is_tool_sourced": _is_tool_sourced,
            },
        )

    # --- Token cost tracking: log and emit cumulative per-turn totals ---
    _turn_token_totals["rounds"] = round_idx + 1
    # Fallback estimate: if the cloud backend didn't return eval_stats
    # (Ollama Cloud proxy doesn't), estimate tokens from the final
    # conversation + the answer/thinking text using chars/4 heuristic.
    if _turn_token_totals["prompt_tokens"] == 0:
        _est_prompt = (
            sum(
                len(str(m.get("content", "") or ""))
                for m in _model_conversation
                if isinstance(m, dict)
            )
            // 4
        )
        _turn_token_totals["prompt_tokens"] = _est_prompt
    if _turn_token_totals["completion_tokens"] == 0:
        _est_completion = (len(final_answer) + len(thinking_text)) // 4
        _turn_token_totals["completion_tokens"] = max(1, _est_completion)
    _turn_token_totals["total_tokens"] = (
        _turn_token_totals["prompt_tokens"] + _turn_token_totals["completion_tokens"]
    )
    with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort
        session_logger.add_token_usage(
            _turn_token_totals["prompt_tokens"],
            _turn_token_totals["completion_tokens"],
        )
    try:
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "token_usage",
                    "prompt_tokens": _turn_token_totals["prompt_tokens"],
                    "completion_tokens": _turn_token_totals["completion_tokens"],
                    "total_tokens": _turn_token_totals["total_tokens"],
                    "rounds": _turn_token_totals["rounds"],
                }
            ),
            websocket,
            session_logger=session_logger,
        )
    except Exception as _e:  # noqa: BLE001
        session_logger.log("token_usage_emit_failed", {"error": str(_e)})

    await svc.manager.send_personal_message(
        json.dumps({"type": "answer_done", "content": final_answer}),
        websocket,
        session_logger=session_logger,
    )
    # Explicitly log the assistant's final response so /sessions/{id}
    # replay can find it even if the websocket_message log was missed
    # (e.g. session closed mid-stream, or the answer_done was sent but
    # the log_message call failed). This is the authoritative record.
    session_logger.log("assistant_response", {"content": final_answer})
    # Turn completed normally — clear the chat-loop checkpoint.
    if _cp is not None:
        try:
            _cp.clear()
        except Exception as e:  # noqa: BLE001
            session_logger.log("checkpoint_clear_failed", {"error": str(e)})
    # Refresh the token meter after the full turn.
    try:
        _total_chars = sum(
            len(str(m.get("content", "") or ""))
            for m in conversation
            if isinstance(m, dict)
        )
        _used_tokens = max(1, _total_chars // 4)
        _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "context_usage",
                    "model": svc.ollama_client.llm_model,
                    "context_window": _ctx_window,
                    "used_tokens": _used_tokens,
                    "available_tokens": max(0, _ctx_window - _used_tokens),
                    "messages": len(conversation),
                }
            ),
            websocket,
            session_logger=session_logger,
        )
    except Exception as _e:  # noqa: BLE001
        session_logger.log("context_usage_emit_failed", {"error": str(_e)})
    session_logger.log(
        "chat_end",
        {
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
        },
    )

    return final_answer
