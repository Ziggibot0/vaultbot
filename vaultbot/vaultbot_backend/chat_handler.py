"""Agentic chat loop — Copilot-style, simple.

The model drives. It can use plan_task / update_task to track its own state
(the harness re-injects the working-memory block every round so the model
always sees its todo list). The framework enforces ONE rule: if the model
works 3+ tool rounds without a plan, execution tools are masked until it
calls plan_task. No phase state machine, no auto-advance, no forced
convergence, no consolidation, no step summaries — just the one-rule plan
gate plus the read-loop detector that observes actual tool results.

What we keep:
- _sanitize_tool_history: convert tool-call rounds to model-safe format
- double-silent failsafe: if the model emits nothing twice, fail loud
- checkpointing: save progress so a crash resumes mid-turn
- answer streaming: answer_chunk / answer_done / thinking events
- per-step RAG: retrieve notes relevant to the current in-progress step
- tool dispatch: execute_agent_tool unchanged (plan_task / update_task /
  custom tools / code tools / etc.)

2026-08-13: removed Hermes-style preflight compression — the small-model
summarization latency cost outweighed the token savings. The hard token cap
(_enforce_token_cap) and proactive tool-result aging (_age_old_tool_results)
remain as the primary context-bounding mechanisms.
"""

from __future__ import annotations

from pathlib import Path


# Leaf-module imports for helpers that were previously deferred-imported
# from main (circular). These are now direct leaf imports — no main dependency.
from fastapi import WebSocket
from services import Services
from task_api import write_partial
from working_memory import TaskList

from config import TUNABLES


# ---------------------------------------------------------------------------
# Extracted leaf modules — imported with underscore aliases so all existing
# call sites (e.g. _check_cancelled, _enforce_token_cap, _sanitize_tool_history)
# work unchanged without a mass rename across the 2,000-line handle_chat body.
# ---------------------------------------------------------------------------
from chat_tool_dispatch import execute_agent_tool  # noqa: F401 — re-exported
from chat_turn_prep import prepare_turn as _prepare_turn
from chat_turn_finalize import finalize_turn as _finalize_turn
from chat_background import run_background_tasks as _run_background_tasks
from chat_loop_state import TurnState
from chat_agentic_loop import run_agentic_loop


# ---------------------------------------------------------------------------
# _prepare_turn, _finalize_turn, and _run_background_tasks were extracted
# into chat_turn_prep.py, chat_turn_finalize.py, and chat_background.py
# respectively. They are imported above so all call sites in handle_chat
# work unchanged.
# ---------------------------------------------------------------------------


async def handle_chat(
    svc: Services, websocket: WebSocket, user_message: str, session_logger
) -> None:
    """Agentic chat: the LLM reasons over the vault, calls tools when it hits
    a gap, and produces a grounded answer. The model drives — no framework
    enforcement, no phases, no auto-planning.
    """
    session_logger.log("chat_begin", {"user_message": user_message})

    # Clear the cancel flag at the start of a new turn so a stale flag
    # from a previous stop doesn't kill the new turn.
    websocket._cancelled = False

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
        session_logger.log(
            "wm_plan_cleared_completed",
            {
                "previous_goal": wm.goal[:100],
                "completed_steps": sum(1 for t in wm.tasks if t.status == "completed"),
                "total_steps": len(wm.tasks),
            },
        )
        wm.clear()

    # Chat-loop checkpoint/resume: if a prior turn was interrupted mid-loop
    # and left a fresh checkpoint, resume it — restore the working-memory plan
    # and tell the model what it already did so it doesn't re-run tools.
    # Cleared on normal completion and /new.  Per-session: each tab gets its
    # own checkpoint file so concurrent sessions don't interfere.
    _session_id = getattr(websocket, "session_id", None)
    if _session_id:
        from chat_checkpoint import ChatLoopCheckpointer as _CLC

        _cp = _CLC.for_session(_session_id, session_logger)
    else:
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
                session_logger.log(
                    "chat_checkpoint_resumed",
                    {
                        "round_idx": _prior.get("round_idx", 0),
                        "tools_already_run": len(_resumed_tool_history),
                    },
                )
        except Exception as e:  # noqa: BLE001 — best-effort
            session_logger.log("chat_checkpoint_resume_failed", {"error": str(e)})

    # Chat-priority: pause the autonomous researcher so it doesn't compete
    # with this interactive turn for the Ollama GPU. Resumed in the finally
    # block so it always clears.
    svc.autonomous_researcher.pause_for_chat()
    try:
        _prep = await _prepare_turn(
            svc, websocket, user_message, session_logger, wm, _cp, _resumed_tool_history
        )
        if _prep is None:
            return  # trivial turn handled
        (
            conversation,
            results,
            system_prompt,
            all_tools,
            custom_schemas,
            procedures_in_context,
            retrieved_paths,
            chat_start_time,
            loop,
            allowed_citations,
        ) = _prep

        # --- Agentic loop: model speaks →’ tool calls (if any) →’ repeat →’ final ---
        # The model decides to call tools, when, and when to stop. The framework
        # NEVER blocks, rejects, or auto-marks anything.
        st = TurnState()
        st._turn_tool_history = list(_resumed_tool_history)
        # Seed the closed-set citation target set from the preflight
        # retrieval. Updated mid-loop when the model calls vault_search /
        # vault_read_note (see chat_loop_tools). Used by the grounding
        # gate in finalize_turn to reject uncited claims.
        st._allowed_citations = allowed_citations
        t0 = loop.time()

        # Working-memory signature cache. conversation[0] is rebuilt only
        # when this changes across rounds, so provider prompt caches see a
        # stable prefix on rounds where the plan didn't move. Sentinel
        # object (never equals a real hash) so the first-round refresh
        # always fires and installs the wm block if present.
        # Token cost tracking: accumulate per-round ollama_stats token counts
        # so we can log and emit a cumulative total per turn. This is the lever
        # for measuring cost-reduction changes — without it, we're tuning blind.
        # Seen-content tracker: per-turn set of {file_path: {"source": str,
        # "lines": (start, end)|None, "round": int}}. Populated by vault_search
        # and code_read. Used to dedup vault_search results so the model doesn't
        # re-search for files it already has, breaking the search loop.
        # Seed with the initial FUSED retrieval results — those files are
        # already in the vault context (conversation[1]) so the model has
        # already "seen" them. This prevents the first vault_search from
        # returning the same files that are already in context.
        for _r in results:
            _fp = _r.get("file_path", "") if isinstance(_r, dict) else ""
            if _fp:
                st._seen_content[_fp] = {
                    "source": "initial_context",
                    "lines": None,
                    "round": -1,
                }
        # --- Findings ledger (anti-amnesia) ---
        # A per-turn list of 1-line entries: "R{n}: {tool} →’ {summary}".
        # Injected into the system prompt every round as "# FINDINGS SO FAR".
        # This survives history budget truncation because it lives in the
        # system prompt, not the conversation history. The model always sees
        # what it already did without re-reading dropped messages. Zero LLM
        # cost (deterministic).
        # Go-find-out escalation: counts consecutive vault_search calls
        # where ALL results were already seen. When this hits the threshold,
        # the harness auto-runs vault_research on the user's question to go
        # find the missing information on the web instead of looping.
        # Track the last vault_search query so go-find-out uses it as the
        # research topic instead of the raw user message. The user message
        # is a conversational instruction ("dude fix the researcher") — not
        # a web search query. The model's own vault_search query is a
        # focused research topic that the search engines can actually use.
        # When go-find-out fires, the research summary is stored here so it
        # can be injected as a system message after the tool results are
        # appended. A system message is more authoritative than a tool result
        # — the model treats it as framework-level instruction, not optional data.

        # Partial-answer crash protection: write the streamed-so-far answer to a
        # temp file so a crash mid-stream doesn't lose it.
        # DEBOUNCED: writing on every chunk (50+ disk writes/sec) creates
        # consumer backpressure that throttles the LLM's streaming throughput.
        # We write at most once per second; the final answer is always written
        # at loop exit.
        import hashlib
        import tempfile
        import time as _time

        partial_dir = Path(tempfile.gettempdir()) / "vaultbot_partials"
        partial_dir.mkdir(parents=True, exist_ok=True)
        partial_id = hashlib.blake2b(
            (user_message + str(_time.time())).encode(), digest_size=12
        ).hexdigest()[:12]
        st.partial_path = partial_dir / f"partial_{partial_id}.md"
        write_partial(st.partial_path, user_message, "", "")

        await run_agentic_loop(
            svc,
            websocket,
            session_logger,
            loop,
            user_message,
            wm,
            conversation,
            all_tools,
            custom_schemas,
            procedures_in_context,
            st,
            _cp,
        )

        st.final_answer = await _finalize_turn(
            svc,
            websocket,
            session_logger,
            loop,
            st.final_answer,
            st.thinking_text,
            st.total_chunks,
            st.round_idx,
            t0,
            st._turn_token_totals,
            st._model_conversation,
            conversation,
            st.partial_path,
            _cp,
            st,
        )

        # --- Grounding retry re-entry (vault-centric provenance) -------
        # finalize_turn flagged the answer as ungrounded (uncited claims
        # against the closed-set). Re-enter the agentic loop ONCE with a
        # reprimand as a user-role turn so the model rewrites the answer
        # with citations. Capped at TUNABLES.max_grounding_retries (1) —
        # after that, finalize_turn shipped the answer + a ⚠️ caution so
        # the user is never left with no answer.
        while getattr(st, "_grounding_failed", False) and getattr(
            st, "_grounding_retry_count", 0
        ) < TUNABLES.max_grounding_retries:
            st._grounding_failed = False
            st._grounding_retry_count += 1
            st.final_answer = ""  # reset so the rewrite replaces, not appends
            # Append the reprimand as a user-role turn — Ollama rejects
            # system messages after user/assistant/tool messages.
            conversation.append(
                {"role": "user", "content": st._grounding_reprimand}
            )
            session_logger.log(
                "grounding_retry_reenter",
                {"retry_count": st._grounding_retry_count},
            )
            await run_agentic_loop(
                svc,
                websocket,
                session_logger,
                loop,
                user_message,
                wm,
                conversation,
                all_tools,
                custom_schemas,
                procedures_in_context,
                st,
                _cp,
            )
            st.final_answer = await _finalize_turn(
                svc,
                websocket,
                session_logger,
                loop,
                st.final_answer,
                st.thinking_text,
                st.total_chunks,
                st.round_idx,
                t0,
                st._turn_token_totals,
                st._model_conversation,
                conversation,
                st.partial_path,
                _cp,
                st,
            )

        await _run_background_tasks(
            svc,
            websocket,
            session_logger,
            loop,
            user_message,
            st.final_answer,
            st.thinking_text,
            st.round_idx,
            st._turn_token_totals,
            st._turn_failed_write_count,
            conversation,
            retrieved_paths,
            chat_start_time,
            wm,
            st._turn_tool_history,
            st._findings,
        )

    finally:
        svc.autonomous_researcher.resume_after_chat()
