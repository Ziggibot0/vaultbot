"""Agentic loop: the model-driven tool-calling while-loop.

Extracted from ``chat_handler.py`` -- ``run_agentic_loop`` is the core
``while round_idx < _MAX_ROUNDS`` loop. It streams the LLM response each
round, executes any tool calls, tracks seen-content / findings / failed-write
/ thought-loop state, and breaks when the model produces a text answer with
no tool calls. It mutates the passed ``TurnState`` in place and appends to
``conversation``.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

import asyncio
import json
import os

from chat_checkpoint import snapshot_working_memory
from chat_context import (
    age_old_tool_results as _age_old_tool_results,
)
from chat_context import (
    enforce_token_cap as _enforce_token_cap,
)
from chat_context import (
    estimate_conv_tokens as _estimate_conv_tokens,
)
from chat_context import (
    project_for_provider as _project_for_provider,
)
from chat_context import (
    round_tool_outcome as _round_tool_outcome,
)
from chat_context import (
    tool_actually_wrote as _tool_actually_wrote,
)
from chat_loop_state import TurnState
from chat_loop_streaming import stream_llm_round
from chat_loop_tools import execute_round_tools
from chat_preflight import check_cancelled as _check_cancelled
from conversation_state import save_history
from error_types import AgentSilentError
from services import Services
from task_api import write_partial
from working_memory import TaskList


def _single_tool_entry_for_round(st: TurnState, round_idx: int) -> dict | None:
    """Return the lone tool-history entry for ``round_idx``, else ``None``."""
    matches = [
        entry
        for entry in st._turn_tool_history
        if isinstance(entry, dict) and entry.get("round") == round_idx
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _update_same_tool_streak(st: TurnState) -> dict[str, object]:
    """Update the same-tool streak from tool history for the current round."""
    current = _single_tool_entry_for_round(st, st.round_idx)
    if current is None:
        st._last_tool_name = ""
        st._consecutive_same_tool = 0
        return {"tool_name": "", "count": 0, "same_result": False}

    tool_name = str(current.get("tool", "") or "")
    current_sig = str(current.get("result_signature", "") or "")
    previous = _single_tool_entry_for_round(st, st.round_idx - 1)
    previous_sig = str(previous.get("result_signature", "") or "") if previous else ""
    previous_tool = str(previous.get("tool", "") or "") if previous else ""

    if (
        previous is not None
        and previous_tool == tool_name
        and previous_sig
        and previous_sig == current_sig
        and st._last_tool_name == tool_name
        and st._consecutive_same_tool > 0
    ):
        st._consecutive_same_tool += 1
    else:
        st._consecutive_same_tool = 1

    st._last_tool_name = tool_name
    same_result = (
        previous is not None
        and previous_tool == tool_name
        and previous_sig == current_sig
    )
    return {
        "tool_name": tool_name,
        "count": st._consecutive_same_tool,
        "same_result": same_result,
    }


async def run_agentic_loop(
    svc: Services,
    websocket,
    session_logger,
    loop,
    user_message: str,
    wm: TaskList,
    conversation: list,
    all_tools: list,
    custom_schemas: list,
    procedures_in_context: list,
    st: TurnState,
    _cp,
) -> None:
    """Run the model-driven agentic loop, mutating ``st`` and ``conversation``.

    The loop ends when the model produces a text answer with no tool calls,
    or when a safety net fires (failed-write streak, thought-loop, MAX_ROUNDS).
    On completion, ``st.final_answer`` holds the accumulated answer.
    """
    try:
        # --- Core loop: the model drives, the harness supports ---
        # The model calls plan_task / update_task if it wants to stay on
        # track; the harness re-injects the wm block every round. The loop
        # ends when the model produces a turn with no tool calls.
        # NO read-loop detector, NO identical-call detector, NO stale-plan
        # detector, NO plan-enforcement gate, NO convergence nudge. The
        # model decides when it's done -- same as Copilot's harness.
        _MAX_ROUNDS = int(os.getenv("VAULTBOT_MAX_ROUNDS", "10000"))
        # Only two safety nets: failed-write streak (genuine thrash) and
        # the MAX_ROUNDS cap (runaway loop). Everything else is the model's
        # decision.
        # 10000 rounds allows multi-day autonomous work sessions. At ~12s
        # per round (typical for cloud models), 10K rounds ≈ 33 hours of
        # continuous work. The failed-write streak detector (3 consecutive
        # failed writes) is the primary anti-thrash guard -- MAX_ROUNDS is
        # just a last-resort cap to prevent a truly infinite loop.
        _WRITE_TOOLS = frozenset(
            {
                "safe_write",
                "code_run",
                "vault_safe_write",
                "vault_append",
                "vault_delete",
                "tool_create",
                "js_safe_write",
                "execute_procedure",
                "vault_research",
            }
        )
        # --- Thought-loop detector (2026-08-15) ---
        # Counts consecutive rounds where the ONLY tool called is "thought".
        # The thought tool is a no-op scratchpad -- it changes nothing in the
        # world. If the model calls it N times in a row without any other
        # tool, it's stuck in a thinking loop ("I need to stop thinking and
        # ACT" -- but it never acts). This was observed in session 15e346b7
        # where the model called thought 20 consecutive times (R30-R47)
        # saying "I'll write the file next time" but never calling a write
        # tool, until the user manually hit Stop.
        #
        # Threshold: 5 consecutive thought-only rounds. At ~4s per round,
        # that's ~20s of zero progress -- enough to be confident it's stuck,
        # not enough to waste the user's money on a long spiral.
        _THOUGHT_LOOP_THRESHOLD = int(os.getenv("VAULTBOT_THOUGHT_LOOP_LIMIT", "5"))
        _SAME_TOOL_STREAK_THRESHOLD = int(
            os.getenv("VAULTBOT_SAME_TOOL_STREAK_LIMIT", "7")
        )
        # After the first same-tool nudge, give the model two more rounds to
        # switch tools / arguments or explain the blocker before forcing exit.
        _SAME_TOOL_STREAK_HARD_EXIT_THRESHOLD = _SAME_TOOL_STREAK_THRESHOLD + 2
        while st.round_idx < _MAX_ROUNDS:
            _check_cancelled(websocket)
            # --- Break condition 1: 3+ consecutive failed writes ---
            # A model hammering a broken tool is genuine thrash. Everything
            # else (reading, searching, planning, thinking) is the model's
            # business -- the framework does not second-guess it.
            if st._turn_failed_write_count >= 3:
                session_logger.log(
                    "loop_exit",
                    {
                        "reason": "failed_write_streak",
                        "round": st.round_idx,
                        "total_tools": st._tool_rounds_executed,
                        "failed_write_count": st._turn_failed_write_count,
                    },
                )
                st.final_answer = (st.final_answer or st.interim_text) + (
                    "\n\n*The loop was stopped after 3 consecutive failed "
                    "writes. The findings above are what I was able to "
                    "determine, but I was unable to complete the task.*"
                )
                break

            # --- Break condition 2: consecutive thought-only rounds ---
            # If the model has called ONLY the thought tool for N
            # consecutive rounds, it's stuck in a thinking loop. Inject a
            # system message that forces it to act, and if it still loops
            # after the nudge, break out.
            if st._consecutive_thought_rounds >= _THOUGHT_LOOP_THRESHOLD:
                session_logger.log(
                    "thought_loop_detected",
                    {
                        "round": st.round_idx,
                        "consecutive_thought_rounds": st._consecutive_thought_rounds,
                    },
                )
                # Inject a firm nudge. 'user' role, NOT 'system' -- Ollama's
                # /v1/chat/completions rejects system messages that appear
                # after user/assistant messages ("system message must be at
                # the beginning"), returning a 500. Same rule as the
                # preflight_chain_injected injection below.
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "FRAMEWORK DIRECTIVE: You have called the "
                            "thought tool "
                            f"{st._consecutive_thought_rounds} consecutive "
                            "times without taking any action. Stop "
                            "thinking and DO the thing you keep saying "
                            "you'll do. Call the actual tool (code_run, "
                            "safe_write, md_safe_replace, vault_safe_write, "
                            "etc.) RIGHT NOW. If you are stuck because a "
                            "tool keeps failing, explain the failure to "
                            "the user in a text response and end the turn. "
                            "Do NOT call the thought tool again."
                        ),
                    }
                )
                # Give it one more chance after the nudge. If it calls
                # thought again, the counter will still be above threshold
                # on the next iteration and we break.
                if st._consecutive_thought_rounds >= _THOUGHT_LOOP_THRESHOLD + 2:
                    session_logger.log(
                        "loop_exit",
                        {
                            "reason": "thought_loop",
                            "round": st.round_idx,
                            "consecutive_thought_rounds": (
                                st._consecutive_thought_rounds
                            ),
                        },
                    )
                    st.final_answer = (st.final_answer or st.interim_text) + (
                        "\n\n*The loop was stopped after "
                        f"{st._consecutive_thought_rounds} consecutive "
                        "thought-only rounds. I was stuck in a thinking "
                        "loop and unable to break out of it. The findings "
                        "above are what I was able to determine.*"
                    )
                    break

            # --- Break condition 3: same tool, same result, no progress ---
            if st._consecutive_same_tool >= _SAME_TOOL_STREAK_THRESHOLD:
                session_logger.log(
                    "same_tool_streak_detected",
                    {
                        "round": st.round_idx,
                        "tool": st._last_tool_name,
                        "consecutive_same_tool": st._consecutive_same_tool,
                    },
                )
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "FRAMEWORK DIRECTIVE: You have called "
                            f"{st._last_tool_name or 'the same tool'} "
                            f"{st._consecutive_same_tool} consecutive times "
                            "and got the same result back. This is not making "
                            "progress. Stop repeating that call. Either use a "
                            "different tool, change the arguments, or explain "
                            "the blocker to the user and end the turn."
                        ),
                    }
                )
                if st._consecutive_same_tool >= _SAME_TOOL_STREAK_HARD_EXIT_THRESHOLD:
                    session_logger.log(
                        "loop_exit",
                        {
                            "reason": "same_tool_streak",
                            "round": st.round_idx,
                            "tool": st._last_tool_name,
                            "consecutive_same_tool": st._consecutive_same_tool,
                        },
                    )
                    st.final_answer = (st.final_answer or st.interim_text) + (
                        "\n\n*The loop was stopped after "
                        f"{st._consecutive_same_tool} consecutive "
                        f"{st._last_tool_name or 'tool'} calls returned the "
                        "same result. I was stuck repeating a non-progressing "
                        "action. The findings above are what I was able to "
                        "determine.*"
                    )
                    break

            # All tools available every round -- no masking, no gate.
            _round_tools = all_tools

            session_logger.log(
                "round_loop_top",
                {
                    "round": st.round_idx,
                    "t_ms": loop.time() * 1000,
                    "conv_msgs": len(conversation),
                },
            )

            # Track round index for tool execution context.
            websocket._chat_round_idx = st.round_idx

            # System prompt is FROZEN after preflight build (Priority A,
            # 2026-08-06). We only refresh the working-memory block when
            # the task list changed \u2014 the model owns that list
            # via plan_task/update_task and needs to see the current state.
            # Everything else that used to be re-injected here (per-step
            # RAG, findings ledger, procedure surface) has been removed
            # from the hot path: it churned the system prompt every round,
            # defeated provider prompt-caching (Anthropic / OpenAI /
            # OpenRouter all cache on prefix), and duplicated content the
            # model already had in conversation history. If the model
            # wants procedure info, it can call vault_search itself.
            #
            # PROMPT-CACHING STRUCTURE (2026-08-15):
            # conversation[0] = stable system prompt (NEVER touched here)
            # conversation[1] = per-query vault context (NEVER touched here)
            # conversation[2] = wm block (updated ONLY when the task list
            #   changes -- when it's unchanged, the entire 3-message prefix
            #   is a cache hit, costing zero input tokens on the cached
            #   portion). This is the key: by NOT rebuilding conversation[0]
            #   every round, the stable prefix stays byte-identical and
            #   the provider's prefix cache fires.
            try:
                _wm_block = wm.render_for_prompt() if wm else ""
                _wm_sig = hash(_wm_block)
                if _wm_sig != st._last_step_rag_key:  # reused as wm-signature cache
                    conversation[2] = {
                        "role": "system",
                        "content": _wm_block,
                    }
                    st._last_step_rag_key = _wm_sig
                # When unchanged: conversation[2] stays as-is from the
                # previous round. The prefix is byte-identical → cache hit.
            except Exception as e:  # noqa: BLE001 \u2014 wm render is best-effort; base system prompt passes through on failure
                session_logger.log("wm_render_failed", {"error": str(e)})
                conversation[2] = {"role": "system", "content": ""}

            # Stream the LLM response for this round.
            round_text = ""
            round_thinking = ""
            round_tool_calls = []
            round_finish_reason: str | None = None
            chunk_count = 0

            # --€--€ Proactive tool-result aging ---------------------------------------
            # Runs EVERY round, before the token cap. Stubs tool results
            # older than N rounds back to a 1-line summary so they don't
            # bloat the prompt and distract the model from the current
            # task. Unlike the token cap (which only fires when total
            # tokens exceed 60K), this is age-based and fires regardless
            # of total size -- the model already processed those results
            # in prior rounds and doesn't need the full payload again.
            # Never breaks tool_call/tool_result pairing (stubs content
            # only); never touches the most recent N rounds.
            _pre_age_msgs = len(conversation)
            _pre_age_tokens = _estimate_conv_tokens(conversation)
            conversation = _age_old_tool_results(
                conversation, session_logger=session_logger, round_idx=st.round_idx
            )
            _post_age_tokens = _estimate_conv_tokens(conversation)

            # --€--€ Hard token cap: GUARANTEED ceiling on prompt size -----------------
            # This runs EVERY round, right before the LLM call. Unlike
            # the context_budgeter (which only budgets vault context) and
            # preflight compression (which only fires once per turn at
            # 50% of context window), this is the enforcement layer that
            # guarantees the TOTAL conversation never exceeds the cap.
            # It prunes old tool-result content (never breaking pairs)
            # and, as a last resort, drops old middle messages.
            # The cap is set to 800K tokens by default -- large enough for
            # cloud models with 1M context to work through long multi-round
            # tasks without losing context. For local models with smaller
            # context windows, the cap still applies as a hard ceiling.
            _pre_cap_msgs = len(conversation)
            _pre_cap_tokens = _estimate_conv_tokens(conversation)
            conversation = _enforce_token_cap(
                conversation, session_logger=session_logger, round_idx=st.round_idx
            )
            _post_cap_tokens = _estimate_conv_tokens(conversation)

            # --€--€ Wait for model to finish loading ----------------------------------
            # The startup-preload thread warms the model configured AT
            # BOOT. If the user switched models via the GUI since then
            # (or the model was evicted after keep_alive expired), the
            # current model is cold and NOTHING is loading it -- polling
            # is_model_loaded() alone would spin the full timeout doing
            # nothing. So we ACTIVELY preload (a 1-token generate that
            # forces Ollama to load the model now), then poll with a
            # heartbeat until it's resident. preload_model() is a no-op
            # (returns True) for cloud backends, so this only loads
            # local Ollama models.
            _model_wait_t0 = loop.time()
            _model_wait_max = float(os.environ.get("VAULTBOT_MODEL_LOAD_WAIT_S", "300"))
            # Kick off an ACTIVE preload in the executor. It returns
            # immediately if the model is already resident; otherwise it
            # blocks (up to 600s) while Ollama loads the model from disk.
            # We poll is_model_loaded() below with a heartbeat so the
            # user sees progress instead of a silent stall.
            _preload_task = loop.run_in_executor(None, svc.ollama_client.preload_model)
            while True:
                _loaded = await loop.run_in_executor(
                    None, svc.ollama_client.is_model_loaded
                )
                if _loaded:
                    break
                _waited = loop.time() - _model_wait_t0
                if _waited >= _model_wait_max:
                    session_logger.log(
                        "model_load_wait_timeout",
                        {"waited_s": _waited},
                    )
                    break
                await svc.manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "heartbeat",
                            "label": "loading model…",
                            "elapsed_ms": int(_waited * 1000),
                            "silent_ms": 0,
                            "chunks": 0,
                        }
                    ),
                    websocket,
                    session_logger=session_logger,
                )
                await asyncio.sleep(2)

            session_logger.log(
                "llm_stream_start",
                {
                    "round": st.round_idx,
                    "conv_msgs": len(conversation),
                    "conv_chars": sum(
                        len(str(m.get("content", "") or "")) for m in conversation
                    ),
                    "est_tokens": _post_cap_tokens,
                    "age_applied": _pre_age_tokens > _post_age_tokens,
                    "cap_applied": _pre_cap_tokens > _post_cap_tokens,
                    "t_ms": loop.time() * 1000,
                },
            )
            # Log the prompt-cache structure: sizes of the stable
            # prefix (system prompt, vault context, wm block) so we
            # can see how much is cacheable vs. how much is re-billed
            # each round. The stable system prompt (conversation[0])
            # is the cacheable prefix; vault context + wm block are
            # cacheable WITHIN a turn (they don't change between
            # rounds unless the task list changes).
            _sys_msgs = [
                m
                for m in conversation
                if isinstance(m, dict) and m.get("role") == "system"
            ]
            if _sys_msgs:
                session_logger.log(
                    "prompt_cache_structure",
                    {
                        "round": st.round_idx,
                        "system_msg_count": len(_sys_msgs),
                        "stable_prompt_chars": len(
                            str(_sys_msgs[0].get("content", "") or "")
                        ),
                        "vault_context_chars": (
                            len(str(_sys_msgs[1].get("content", "") or ""))
                            if len(_sys_msgs) > 1
                            else 0
                        ),
                        "wm_block_chars": (
                            len(str(_sys_msgs[2].get("content", "") or ""))
                            if len(_sys_msgs) > 2
                            else 0
                        ),
                        "cacheable_prefix_chars": sum(
                            len(str(m.get("content", "") or "")) for m in _sys_msgs
                        ),
                    },
                )
            # ── Provider-safe message projection ──────────────────────
            # Strip all internal bookkeeping fields (thinking, timestamp,
            # digested, etc.) before sending to ANY provider. This is
            # universal — no per-model heuristics. It does two things:
            #
            # 1. Preserves the KV cache: the token sequence for prior
            #    messages stays stable across rounds (no new thinking
            #    content appended), so Ollama's prefix cache hits instead
            #    of re-evaluating the entire prompt every round.
            #
            # 2. Prevents generation corruption: the model never sees its
            #    own prior reasoning (thinking) as regular text, which
            #    pollutes attention and causes degenerate repetition.
            #
            # For glm-via-Ollama (which returns empty on tool_calls/tool
            # role), we also flatten tool calls to system messages. This
            # is a protocol bug specific to that model, not a general
            # heuristic — see /memories/glm-ollama-tool-calls-broken.md.
            _model_name = (svc.ollama_client.llm_model or "").lower()
            _client_cls = svc.ollama_client.__class__.__name__.lower()
            _flatten = os.getenv(
                "VAULTBOT_FORCE_SANITIZE_TOOL_HISTORY", "0"
            ) == "1" or ("ollama" in _client_cls and "glm" in _model_name)
            st._model_conversation = _project_for_provider(
                conversation, flatten_tool_calls=_flatten
            )

            (
                round_text,
                round_thinking,
                round_tool_calls,
                round_finish_reason,
                chunk_count,
            ) = await stream_llm_round(
                svc,
                websocket,
                session_logger,
                loop,
                user_message,
                _round_tools,
                st,
            )

            session_logger.log(
                "agent_round",
                {
                    "round": st.round_idx,
                    "chunk_count": chunk_count,
                    "text_length": len(round_text),
                    "tool_calls": len(round_tool_calls),
                },
            )

            # Append the assistant's turn to the conversation.
            # Timestamp (issue #85 — temporal awareness): persisted so the
            # conversation index can surface recency and the LLM can tell
            # how old a turn is. Backward-compatible (missing = legacy turn).
            assistant_msg = {
                "role": "assistant",
                "content": round_text,
                "timestamp": loop.time(),
            }
            if round_thinking:
                assistant_msg["thinking"] = round_thinking
            if round_tool_calls:
                assistant_msg["tool_calls"] = round_tool_calls
            conversation.append(assistant_msg)

            # --------------------------------------------------------------------------
            # Model produced text (no tool calls) →' accept as final answer.
            # No dangling detection, no plan-continuation nudge, no text
            # inspection. The model decides when it's done.
            # --------------------------------------------------------------------------
            if not round_tool_calls:
                if round_text.strip():
                    # --€--€ Plan-continuation guard --€--€
                    # The model produced text without tool calls. Under the
                    # "model drives" architecture, the framework does NOT
                    # intervene when the model stops with unfinished tasks.
                    # The model is responsible for deciding when it's done.
                    # (test_no_framework_intervention_on_unfinished_plan
                    #  enforces this -- no plan-completion checks here.)
                    st.final_answer = round_text
                    session_logger.log(
                        "turn_done",
                        {
                            "round": st.round_idx,
                            "answer_length": len(st.final_answer),
                            "tool_rounds": st._tool_rounds_executed,
                            "finish_reason": round_finish_reason or "stop",
                        },
                    )
                    session_logger.log(
                        "loop_exit",
                        {
                            "reason": "natural_done",
                            "round": st.round_idx,
                            "total_tools": st._tool_rounds_executed,
                            "total_text_chars": len(st.final_answer),
                            "findings_count": len(st._findings),
                            "plan_had_tasks": wm.has_plan(),
                        },
                    )
                    break

                # Double-silent failsafe: model returned nothing twice.
                else:
                    if not st._double_silent_once:
                        st._double_silent_once = True
                        session_logger.log("silent_turn_retry", {"round": st.round_idx})
                        conversation.append(
                            {
                                "role": "user",
                                "content": "(no response received -- please reply)",
                            }
                        )
                        st.round_idx += 1
                        continue
                    session_logger.log(
                        "agent_silent_fail_loud",
                        {
                            "round": st.round_idx,
                            "tool_rounds": st._tool_rounds_executed,
                        },
                    )
                    raise AgentSilentError(
                        "Model returned nothing on two consecutive turns. Please retry."
                    )

            # Model called tools →' execute them and feed results back.
            st._tool_rounds_executed += 1
            st._double_silent_once = False

            # Accumulate non-final round text as INTERIM narration so the
            # partial file captures all streamed text without polluting the
            # persisted final answer (issue #388).
            if round_text.strip() and round_text.strip() != ".":
                st.interim_text += round_text

            # Snapshot the conversation length so the findings ledger can
            # read back exactly the tool messages appended THIS round
            # (issue #386).
            _pre_tool_msgs = len(conversation)

            all_tools, custom_schemas = await execute_round_tools(
                svc,
                websocket,
                session_logger,
                loop,
                user_message,
                conversation,
                round_tool_calls,
                st,
                all_tools,
                custom_schemas,
                wm,
                procedures_in_context,
            )

            # --- Failed-write tracking (the ONLY safety net) ---
            # Count failed writes. 3 consecutive failed writes = genuine
            # thrash (model hammering a broken tool). This is the only
            # framework-level break condition besides MAX_ROUNDS. Tool
            # results are read back from the tool messages appended this
            # round (issue #386: same payloads the findings ledger uses).
            for tc in round_tool_calls:
                _tn = (tc.get("function", {}) or {}).get("name", "")
                if _tn in _WRITE_TOOLS:
                    _tr = None
                    for _m in reversed(conversation):
                        if _m.get("role") == "tool" and _m.get("tool_name") == _tn:
                            try:
                                _tr = json.loads(_m.get("content", "{}"))
                            except (json.JSONDecodeError, TypeError):
                                _tr = {}
                            break
                    # A procedure-suggestion result is a *nudge*, not a write
                    # attempt. The gate intercepted the raw call and returned a
                    # suggestion dict (procedure_suggestion / proceed_keyword)
                    # instead of executing it. Counting that as a failed write
                    # pollutes the anti-thrash counter and can trip the
                    # failed_write_streak break on a legitimate turn. Skip it.
                    if isinstance(_tr, dict) and (
                        "procedure_suggestion" in _tr or "proceed_keyword" in _tr
                    ):
                        continue
                    if not _tool_actually_wrote(_tn, _tr):
                        st._turn_failed_write_count += 1
                        session_logger.log(
                            "failed_write_detected",
                            {
                                "round": st.round_idx,
                                "tool": _tn,
                                "result_keys": list(_tr.keys())
                                if isinstance(_tr, dict)
                                else None,
                            },
                        )
                    else:
                        # A successful write resets the failed-write counter.
                        st._turn_failed_write_count = 0

            # --- Findings ledger: append a 1-line entry for this round ---
            # Derive each tool's outcome from its ACTUAL result payload
            # (the tool messages appended by execute_round_tools this
            # round), not from the write-failure heuristic -- a failed
            # read/search must not be reported as "ok", and a successful
            # write tool must not be reported as "write_failed" when a
            # DIFFERENT write tool in the same round failed (issue #386).
            _outcomes: list[str] = []
            for _m in conversation[_pre_tool_msgs:]:
                if not (isinstance(_m, dict) and _m.get("role") == "tool"):
                    continue
                _tn = _m.get("tool_name", "?")
                try:
                    _tr = json.loads(_m.get("content", "{}"))
                except (json.JSONDecodeError, TypeError):
                    _tr = {}
                _outcomes.append(f"{_tn}={_round_tool_outcome(_tn, _tr)}")
            _finding_entry = (
                f"R{st.round_idx}: "
                f"{', '.join(_outcomes) if _outcomes else '(no tools)'}"
            )
            if round_text.strip():
                _finding_entry += f" | text: {round_text.strip()[:80]}"
            st._findings.append(_finding_entry)
            session_logger.log(
                "findings_ledger_updated",
                {
                    "round": st.round_idx,
                    "entry": _finding_entry,
                    "total_findings": len(st._findings),
                },
            )

            # NO mid-loop truncation. Compression/pruning is a preflight
            # event (once per turn, before the first LLM call) -- never
            # inside the tool-call loop. Mid-loop truncation destroys the
            # tool results the model JUST received and drops
            # tool_call/tool_result pairs the provider expects to be
            # paired. This is the Hermes Agent shape: within a turn, the
            # conversation only grows; between turns is when we compact.
            # See /memories/repo/hermes-agent-lessons.md.

            # --- Thought-loop tracking (2026-08-15) ---
            # Count consecutive rounds where the ONLY tool called is
            # "thought". Reset to 0 if any non-thought tool was called
            # (the model took action) or if no tools were called (the
            # model produced a text answer -- the turn is ending).
            _round_tool_names = [
                tc.get("function", {}).get("name", "?") for tc in round_tool_calls
            ]
            if _round_tool_names and all(t == "thought" for t in _round_tool_names):
                st._consecutive_thought_rounds += 1
            else:
                st._consecutive_thought_rounds = 0

            _same_tool = _update_same_tool_streak(st)
            session_logger.log(
                "same_tool_streak_updated",
                {
                    "round": st.round_idx,
                    "tool": _same_tool["tool_name"],
                    "consecutive_same_tool": _same_tool["count"],
                    "same_result": _same_tool["same_result"],
                },
            )

            # Loop back.
            st.round_idx += 1
            _check_cancelled(websocket)

            # Chat-loop checkpoint: snapshot the in-flight turn.
            # Includes the findings ledger so a restart mid-turn restores
            # the model's progress awareness, not just the partial answer.
            if _cp is not None:
                try:
                    _cp.save(
                        {
                            "user_message": user_message,
                            "round_idx": st.round_idx,
                            "accumulated": st.interim_text + st.final_answer,
                            "thinking": st.thinking_text,
                            "tool_history": st._turn_tool_history,
                            "working_memory": snapshot_working_memory(wm),
                            "findings_ledger": list(st._findings),
                        }
                    )
                    session_logger.log(
                        "checkpoint_saved",
                        {
                            "round": st.round_idx,
                            "findings_count": len(st._findings),
                            "plan_has_tasks": wm.has_plan(),
                        },
                    )
                except Exception as e:  # noqa: BLE001 -- checkpoint is best-effort; the chat loop must not crash on save failure
                    session_logger.log("chat_checkpoint_save_failed", {"error": str(e)})

            # Stream history persistence: save the conversation-so-far to
            # disk after each tool round so a crash mid-turn doesn't lose
            # the entire turn's context. Best-effort: never breaks the loop.
            try:
                # Per-message content cap for on-disk history. Was 4000 --
                # too aggressive: a vault_read_note or code_read result gets
                # chopped in the persisted copy, so the model on the NEXT
                # round (or after a restart) sees a stub where its own read
                # was. Default raised to 40000 chars, configurable via env.
                _persist_cap = int(os.getenv("VAULTBOT_HISTORY_MSG_CAP", "40000"))
                _hist_so_far = []
                for _m in conversation:
                    if _m.get("role") == "system":
                        continue
                    _m2 = dict(_m)
                    _m2.pop("thinking", None)
                    _c = _m2.get("content")
                    if isinstance(_c, str) and len(_c) > _persist_cap:
                        _m2["content"] = (
                            _c[:_persist_cap] + "\n[...truncated in history...]"
                        )
                    _hist_so_far.append(_m2)
                if len(_hist_so_far) > 0:
                    save_history(
                        _hist_so_far,
                        session_id=getattr(websocket, "session_id", None),
                    )
            except Exception as _e:  # noqa: BLE001 -- stream history is best-effort; the loop must not crash on save failure
                session_logger.log("stream_history_save_failed", {"error": str(_e)})

            # --- Round summary (diagnostic logging) ---
            # One event per round with everything needed to understand it
            # without reading any other event. Grep "round_summary" to
            # reconstruct a stalled session from the session log alone.
            session_logger.log(
                "round_summary",
                {
                    "round": st.round_idx,
                    "tools_called": [
                        tc.get("function", {}).get("name", "?")
                        for tc in round_tool_calls
                    ],
                    "tool_count": len(round_tool_calls),
                    "text_chars": len(round_text),
                    "thinking_chars": len(round_thinking),
                    "failed_write_count": st._turn_failed_write_count,
                    "consecutive_thought_rounds": st._consecutive_thought_rounds,
                    "last_tool_name": st._last_tool_name,
                    "consecutive_same_tool": st._consecutive_same_tool,
                    "has_plan": wm.has_plan(),
                    "findings_count": len(st._findings),
                    "conv_chars": sum(
                        len(str(m.get("content", "") or "")) for m in conversation
                    ),
                    "conv_msgs": len(conversation),
                },
            )

        # Round-cap safety log: if we exited the loop by hitting _MAX_ROUNDS
        # (not via a natural break), record it so it's visible in session logs.
        if st.round_idx >= _MAX_ROUNDS:
            session_logger.log(
                "round_cap_hit",
                {
                    "round": st.round_idx,
                    "answer_length": len(st.final_answer),
                    "tool_rounds": st._tool_rounds_executed,
                },
            )
            session_logger.log(
                "loop_exit",
                {
                    "reason": "max_rounds",
                    "round": st.round_idx,
                    "total_tools": st._tool_rounds_executed,
                    "total_text_chars": len(st.final_answer),
                    "findings_count": len(st._findings),
                    "failed_write_count": st._turn_failed_write_count,
                },
            )

    except Exception as e:  # noqa: BLE001
        session_logger.log_exception(e, context="handle_chat_agentic_loop")
        write_partial(
            st.partial_path,
            user_message,
            st.interim_text + st.final_answer,
            st.thinking_text,
        )
        session_logger.log(
            "partial_answer_saved_on_crash",
            {
                "partial_path": str(st.partial_path),
                "answer_chars": len(st.final_answer),
            },
        )
        raise
    finally:
        # If the answer completed normally, clean up the partial file.
        if st.final_answer and len(st.final_answer) > 50:
            try:
                if st.partial_path.exists():
                    st.partial_path.unlink()
            except Exception as e:  # noqa: BLE001
                session_logger.log("partial_cleanup_failed", {"error": str(e)})
