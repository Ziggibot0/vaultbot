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
        elif tool == "code_write":
            # code_write is a forbidden file-modification path (no doc
            # gate). Flag as a potential bypass.
            bypassed = True
        elif tool == "code_run":
            # code_run is read-only by default — the guard preamble blocks
            # file writes in the subprocess (issue #207). Only a call that
            # explicitly opted out via allow_write=True can actually modify
            # files, so only that is a potential gate bypass (issue #387).
            if e.get("allow_write"):
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

    # --- Grounding OBSERVATION (was: closed-set citation gate) ----------
    # The hard gate is GONE. It used to re-enter the agentic loop with a
    # reprimand when the drafted answer had uncited sentences (and append a
    # scary caution after the retry cap) — burning a full LLM round and, on
    # vaults with an empty/thin index (fresh installs), flagging nearly
    # every answer. That was the last mechanism that could suppress or
    # delay a drafted answer, and it is what users experienced as "the bot
    # thinks and calls tools but never speaks."
    #
    # What remains is observational only: score the answer for the trust
    # badge / Sources block, and repair mangled wikilinks (pure usability —
    # the user gets clickable citations). Nothing here can alter the
    # answer's text or block delivery. Provenance is surfaced, not
    # enforced; per ADR-0004 (ratchets-not-gates) enforcement returns as a
    # badge upgrade once entailment is reimplemented correctly as a
    # background layer.
    _allowed = getattr(st, "_allowed_citations", None) or {}
    _score: dict = {}
    _confidence: dict = {}
    _is_idk = False
    _graph_lookup: Callable[[str], bool] | None = None
    _is_tool_sourced = is_tool_sourced(getattr(st, "_turn_tool_history", None))
    try:

        def _graph_lookup(_wl):
            note = svc.vault_graph.get_note(_wl)
            return bool(isinstance(note, dict) and note.get("file_path"))

    except Exception:  # noqa: BLE001 — best-effort
        _graph_lookup = None
    if final_answer and len(final_answer) > 50:
        try:
            from citation_gate import detect_idk, score_grounding

            _is_idk = detect_idk(final_answer)

            # Wikilink repair (issue #335): rewrite mangled stems against
            # the closed set so the user gets clickable citations. Pure
            # text repair — can never invent a citation, only fix typos of
            # notes the model was already shown.
            if _allowed and not _is_idk:
                try:
                    from custom_tools.wikilink_repair import (
                        repair_wikilinks_in_text,
                    )

                    _repaired_answer, _repair_pairs = repair_wikilinks_in_text(
                        final_answer, _allowed
                    )
                    if _repair_pairs:
                        final_answer = _repaired_answer
                        session_logger.log(
                            "wikilinks_repaired",
                            {"repairs": _repair_pairs[:10]},
                        )
                except Exception as _e:  # noqa: BLE001 — repair is best-effort
                    session_logger.log("wikilink_repair_failed", {"error": str(_e)})

            _score = score_grounding(final_answer, _allowed, _graph_lookup, [])
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
                    "is_idk": _is_idk,
                    "is_tool_sourced": _is_tool_sourced,
                    "gate": "observational",
                },
            )
        except Exception as _e:  # noqa: BLE001 — best-effort scoring
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

    # --- Provenance surface: trust badge + Sources block ----------------
    # The synchronous claim-entailment delivery gate (issue #408) was
    # REMOVED from the critical path: it ran the Verify-Answer-Entailment
    # procedure synchronously on every cited answer and, on any
    # unsupported/unverifiable verdict or verifier failure, replaced the
    # drafted answer with a canned truth-gap non-answer. That left users
    # with "the model called tools and thought but emitted no words" —
    # the drafted answer was silently discarded. Entailment is tabled
    # until it can be implemented correctly (as a background layer, per
    # the procedure's own design). The answer ALWAYS reaches the user now;
    # the grounding score (closed-set citation check above) still drives
    # the trust badge and Sources block so provenance remains visible.
    #
    # After grounding enforcement, append a POSITIVE affordance so a scholar
    # can see where the answer came from: a one-line trust badge + a
    # "## Sources" list of clickable [[wikilinks]]. This is the "I can see
    # where the answers are coming from" moment. Only added when the answer
    # actually cites vault notes AND is not an IDK answer (a greeting or
    # "I don't know" gets no block — the citations in an IDK answer are
    # just the irrelevant notes the model was told it could cite, not
    # real provenance for a factual claim).
    if _score and not _is_idk and not _is_tool_sourced:
        try:
            from citation_gate import build_sources_block, build_trust_badge

            _badge = build_trust_badge(_score, _confidence) if _score else ""
            _sources = build_sources_block(final_answer, _allowed)
            if _sources:
                final_answer = f"{final_answer}\n\n{_badge}\n\n{_sources}"
                if _confidence:
                    svc.calibration_tracker.log_answer_confidence(
                        final_answer, _confidence
                    )
                session_logger.log(
                    "provenance_surface",
                    {
                        "badge": _badge,
                        "sources": len(_sources.splitlines()) - 1,
                        "confidence": _confidence.get("calibrated_confidence"),
                    },
                )
        except Exception as _e:  # noqa: BLE001 — best-effort
            session_logger.log("provenance_surface_failed", {"error": str(_e)})
    else:
        session_logger.log(
            "provenance_surface_skipped_idk",
            {
                "is_idk": _is_idk,
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
        json.dumps(
            {
                "type": "answer_done",
                "content": final_answer,
                "confidence": _confidence if _confidence else None,
            }
        ),
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
    # Refresh the token meter after the full turn. Routed through the
    # shared _emit_context_usage helper (chat_turn_prep) so a hung
    # /api/show probe can never stall finalize — the old inline copy
    # called the blocking context_window() directly on the event loop.
    from chat_turn_prep import _emit_context_usage

    await _emit_context_usage(svc, websocket, session_logger, conversation)
    session_logger.log(
        "chat_end",
        {
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
        },
    )

    return final_answer
