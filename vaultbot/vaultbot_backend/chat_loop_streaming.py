"""LLM streaming for one agentic-loop round.

Extracted from ``chat_agentic_loop.py`` — ``stream_llm_round`` streams the
LLM response for a single round: it runs the ``sync_stream`` generator in an
executor, processes chunks (heartbeat, eval_stats, thinking/text/tool_calls
accumulation, debounced partial write), and returns the accumulated round
state. It mutates ``st.total_chunks``, ``st.thinking_text``,
``st._turn_token_totals``, and ``st._last_partial_write_s``.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

import asyncio
import json
import time
import time as _time

from chat_loop_state import TurnState
from services import Services
from task_api import write_partial


async def stream_llm_round(
    svc: Services,
    websocket,
    session_logger,
    loop,
    user_message: str,
    _round_tools: list,
    st: TurnState,
) -> tuple[str, str, list, str | None, int]:
    """Stream one LLM round and return the accumulated round state.

    Returns (round_text, round_thinking, round_tool_calls,
    round_finish_reason, chunk_count).
    """
    round_text = ""
    round_thinking = ""
    round_tool_calls = []
    round_finish_reason: str | None = None
    chunk_count = 0
    try:

        def sync_stream():
            session_logger.log(
                "ollama_chat_call_enter",
                {
                    "round": st.round_idx,
                    "t_ms": time.time() * 1000,
                },
            )
            for chunk in svc.ollama_client.chat(
                st._model_conversation, tools=_round_tools, stream=True
            ):
                yield chunk
            session_logger.log(
                "ollama_chat_call_exit",
                {
                    "round": st.round_idx,
                    "t_ms": time.time() * 1000,
                },
            )

        gen = sync_stream()
        round_t0 = loop.time()
        last_chunk_at = loop.time()
        while True:
            next_chunk_task = loop.run_in_executor(
                None, lambda: next(gen, {"done": True})
            )
            chunk = None
            while chunk is None:
                try:
                    chunk = await asyncio.wait_for(
                        asyncio.shield(next_chunk_task), timeout=3.0
                    )
                except TimeoutError:
                    elapsed = int((loop.time() - round_t0) * 1000)
                    since = int((loop.time() - last_chunk_at) * 1000)
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "label": f"thinking (round {st.round_idx + 1})",
                                "elapsed_ms": elapsed,
                                "silent_ms": since,
                                "chunks": chunk_count,
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                except asyncio.CancelledError:
                    # Don't call gen.close() here — the generator is
                    # running in an executor thread and close() from
                    # the main thread is unsafe (can raise RuntimeError).
                    # The HTTP response was already closed by
                    # cancel_active_stream() in ws.py, so the generator
                    # will exit on its own when iter_lines() raises.
                    raise
            if (
                chunk.get("done")
                and not chunk.get("response")
                and not chunk.get("tool_calls")
            ):
                if chunk.get("finish_reason"):
                    round_finish_reason = chunk["finish_reason"]
                break
            if chunk.get("eval_stats"):
                _es = chunk["eval_stats"]
                _prompt_tps = 0.0
                _gen_tps = 0.0
                if _es.get("prompt_eval_duration", 0) > 0:
                    _prompt_tps = _es["prompt_eval_count"] / (
                        _es["prompt_eval_duration"] / 1e9
                    )
                if _es.get("eval_duration", 0) > 0:
                    _gen_tps = _es["eval_count"] / (_es["eval_duration"] / 1e9)
                await svc.manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "ollama_stats",
                            "load_duration_ms": _es.get("load_duration", 0) / 1e6,
                            "prompt_eval_count": _es.get("prompt_eval_count", 0),
                            "prompt_eval_duration_ms": _es.get(
                                "prompt_eval_duration", 0
                            )
                            / 1e6,
                            "prompt_tokens_per_s": round(_prompt_tps, 1),
                            "eval_count": _es.get("eval_count", 0),
                            "eval_duration_ms": _es.get("eval_duration", 0) / 1e6,
                            "gen_tokens_per_s": round(_gen_tps, 1),
                            "total_duration_ms": _es.get("total_duration", 0) / 1e6,
                        }
                    ),
                    websocket,
                    session_logger=session_logger,
                )
                # Accumulate token counts for per-turn cost tracking.
                st._turn_token_totals["prompt_tokens"] += _es.get(
                    "prompt_eval_count", 0
                )
                st._turn_token_totals["completion_tokens"] += _es.get("eval_count", 0)
                continue
            chunk_count += 1
            st.total_chunks += 1
            last_chunk_at = loop.time()
            thinking = chunk.get("thinking", "")
            text = chunk.get("response", "")
            tcs = chunk.get("tool_calls", [])
            if thinking:
                round_thinking += thinking
                st.thinking_text += thinking
                await svc.manager.send_personal_message(
                    json.dumps({"type": "thinking", "content": thinking}),
                    websocket,
                    session_logger=session_logger,
                )
            if text:
                round_text += text
                await svc.manager.send_personal_message(
                    json.dumps({"type": "answer_chunk", "content": text}),
                    websocket,
                    session_logger=session_logger,
                )
                # Debounced partial write: at most once per second.
                # Per-chunk writes create disk I/O backpressure that
                # throttles the LLM's streaming throughput.
                _now_s = _time.time()
                if _now_s - st._last_partial_write_s >= 1.0:
                    write_partial(
                        st.partial_path,
                        user_message,
                        st.final_answer + round_text,
                        st.thinking_text,
                    )
                    st._last_partial_write_s = _now_s
            if tcs:
                round_tool_calls.extend(tcs)
    except Exception as e:
        session_logger.log_exception(e, context="ollama_client.chat")
        if round_text:
            write_partial(
                st.partial_path,
                user_message,
                st.final_answer + round_text,
                st.thinking_text,
            )
        from diagnostics import classify_error

        diag = classify_error(e, {"stage": "thinking"})
        await svc.manager.send_personal_message(
            json.dumps({"type": "problem", "diagnosis": diag.to_dict()}),
            websocket,
            session_logger=session_logger,
        )
        raise

    return (
        round_text,
        round_thinking,
        round_tool_calls,
        round_finish_reason,
        chunk_count,
    )
