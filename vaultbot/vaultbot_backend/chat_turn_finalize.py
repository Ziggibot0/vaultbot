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

import json
from pathlib import Path

from chat_loop_state import TurnState
from config import TUNABLES
from services import Services


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
    _allowed = getattr(st, "_allowed_citations", None) or {}
    _grounding_caution = ""
    _graph_lookup = None
    try:
        _graph_lookup = lambda _wl: bool(
            svc.vault_graph.get_note(_wl)
            and svc.vault_graph.get_note(_wl).get("file_path")
        )
    except Exception:  # noqa: BLE001 — best-effort
        _graph_lookup = None
    if final_answer and len(final_answer) > 50:
        try:
            from citation_gate import score_grounding, build_reprimand

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
                },
            )
            if _score["failed"]:
                # Hard gate: flag for retry if under the cap.
                _retries = getattr(st, "_grounding_retry_count", 0)
                if _retries < TUNABLES.max_grounding_retries:
                    st._grounding_failed = True
                    st._grounding_reprimand = build_reprimand(_score, _allowed)
                    session_logger.log(
                        "grounding_retry_requested",
                        {"retry_count": _retries, "max": TUNABLES.max_grounding_retries},
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
            elif _score["grounding_score"] < 0.5 and _score["total_wikilinks"] > 0:
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
    _turn_token_totals["estimated"] = True  # flag that these are estimates
    session_logger.log("token_usage", _turn_token_totals)
    # Persist to the session-level accumulator for cross-turn totals.
    try:
        session_logger.add_token_usage(
            _turn_token_totals["prompt_tokens"],
            _turn_token_totals["completion_tokens"],
        )
    except Exception:  # noqa: BLE001 — best-effort
        pass
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