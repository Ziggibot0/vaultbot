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

    # --- Grounding enforcement: verify the answer is grounded in vault ---
    # This is the code-level fix for Problem #1 from
    # [[Why-Vault-Knowledge-Loses-to-Model-Weights]]: no enforcement
    # mechanism. We check that the answer cites vault notes (wikilinks)
    # and that those wikilinks actually exist. An answer with zero
    # wikilinks is flagged as ungrounded. This is logged for calibration
    # and surfaced to the user as a caution when the grounding score
    # is below threshold.
    _grounding_score = 1.0
    _grounding_caution = ""
    if final_answer and len(final_answer) > 50:
        try:
            import re as _re

            # Extract all [[wikilinks]] from the answer
            _wikilinks = _re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", final_answer)
            _unique_links = list(dict.fromkeys(_wikilinks))  # dedup, preserve order
            # Verify each wikilink exists in the vault graph
            _found = 0
            _missing = []
            for _wl in _unique_links[:20]:  # check up to 20
                _node = svc.vault_graph.get_note(_wl)
                if _node and _node.get("file_path"):
                    _found += 1
                else:
                    _missing.append(_wl)
            _total = len(_unique_links)
            if _total == 0:
                # Zero wikilinks — answer is completely ungrounded
                _grounding_score = 0.0
                _grounding_caution = (
                    "\n\n> ⚠️ **Grounding check**: This answer cites no vault notes. "
                    "It may be from model weights rather than your vault. "
                    "Consider asking me to verify or research this topic."
                )
            else:
                _grounding_score = _found / _total
                if _grounding_score < 0.5:
                    _grounding_caution = (
                        f"\n\n> ⚠️ **Grounding check**: Only {_found}/{_total} "
                        f"cited notes were found in the vault. "
                        f"Missing: {', '.join(_missing[:5])}. "
                        f"This answer may be partially ungrounded."
                    )
            session_logger.log(
                "grounding_check",
                {
                    "total_wikilinks": _total,
                    "found": _found,
                    "missing": _missing[:10],
                    "grounding_score": round(_grounding_score, 2),
                    "caution": bool(_grounding_caution),
                },
            )
            # Append the caution to the answer so the user sees it
            if _grounding_caution:
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