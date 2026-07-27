"""Agentic chat loop + tool dispatch — extracted from main.py.

This module owns the two biggest functions in the VaultBot backend:

- ``handle_chat`` — the "Jarvis loop": the LLM reasons over the vault,
  calls tools (research, search, gaps, status, self-improvement, textbook
  reads, custom agent-authored tools) when it hits a gap, and produces a
  grounded answer. Includes post-answer housekeeping (embedding-drift
  feedback, lazy de-fluff, history persistence, goal/self-model updates,
  pattern extraction).
- ``execute_agent_tool`` — single tool-call dispatcher for the chat LLM.

Both functions were lifted verbatim from ``main.py`` with two mechanical
transformations:

1. Each takes a ``svc: Services`` registry as its first parameter and
   reads the formerly-module-level singletons via attribute access
   (``svc.ollama_client``, ``svc.fused_retriever``, ...) instead of as
   free variables.
2. Calls back into ``main.py`` helper functions (``_send_progress``,
   ``_run_with_heartbeat``, ``_write_partial``, ``_tool_result_summary``,
   ``_weave_textbook_notes``, ``_existing_note_titles``, ``_link_outbound``,
   ``_cross_link_textbooks``) are deferred-imported inside the function
   body to break the ``main`` ↔ ``chat_handler`` import cycle
   (``main`` imports ``chat_handler`` at module init; if ``chat_handler``
   imported ``main`` at module init the cycle would explode).

The module-level imports here are leaf modules only (``services``,
``abstract_context``, ``agent_tools``, ``vault_graph``, ``procedure_tracker``,
``concept_card``, ``lazy_condenser``) — never ``main``.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from abstract_context import build_abstract_context
from agent_tools import META_TOOL_DEFINITIONS, TOOL_DEFINITIONS, build_system_prompt

# Leaf-module imports for helpers that were previously deferred-imported
# from main (circular). These are now direct leaf imports — no main dependency.
from chat_helpers import run_with_heartbeat, send_progress, tool_result_summary
from fastapi import WebSocket
from procedure_tracker import interpret_validation_result, parse_procedures_from_results

# Step-gate runtime: compile-then-execute pattern for procedural notes.
# See [[Procedural-Bootstrap-and-Evolution-Plan]] and step_gate_runtime.py.
from procedure_compiler import compile_from_text, compile_procedure
from step_gate_runtime import execute_procedure as execute_step_gate
from services import Services
from task_api import write_partial
from vault_graph import build_graph_context
from weaving import (
    cross_link_textbooks,
    existing_note_titles,
    link_outbound,
    weave_textbook_notes,
)


async def handle_chat(svc: Services, websocket: WebSocket,
                     user_message: str, session_logger) -> None:
    """Agentic chat: the LLM reasons over the vault, calls tools (research,
    search, gaps, status) when it hits a gap, and produces a grounded answer.

    This is the Jarvis loop — the LLM self-directs instead of shrugging.
    """
    # Module-level imports from chat_helpers, task_api, weaving — no longer
    # deferred from main (circular dependency eliminated).
    session_logger.log("chat_begin", {"user_message": user_message})

    # Chat-priority: pause the autonomous researcher so it doesn't compete
    # with this interactive turn for the Ollama GPU. On a single-GPU laptop
    # the user's embedding + LLM calls would otherwise queue behind the
    # researcher's background synthesis, making the chat appear to hang.
    # Resumed in the finally block below so it always clears (even on
    # cancel/error). The researcher skips its cycle while this is set.
    svc.autonomous_researcher.pause_for_chat()

    # Calibration: detect if this message is a correction of the previous
    # answer. Sean's corrections are ground truth for calibrating automated
    # quality gates. See [[Calibration-via-Operator-Feedback]].
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
    except Exception as e:
        session_logger.log("correction_detection_failed", {"error": str(e)})
    await svc.manager.send_personal_message(json.dumps({"type": "status", "content": "Searching vault..."}), websocket, session_logger=session_logger)
    loop = asyncio.get_event_loop()

    # Keep the in-memory vault graph current with disk before retrieval.
    # The intended design was an incremental diff (cost proportional to
    # changed files); VaultGraph.refresh() is now mtime-gated and only re-reads
    # files that changed since the last refresh, so the common no-edit case is
    # a cheap stat-only scan and notes created/edited earlier in the chat still
    # surface. This keeps the vault graph current with disk before retrieval.
    try:
        _t_graph = loop.time()
        await loop.run_in_executor(None, svc.vault_graph.refresh)
        session_logger.log("graph_refreshed", {
            "node_count": len(svc.vault_graph.nodes),
            "duration_ms": (loop.time() - _t_graph) * 1000,
        })
    except Exception as e:
        session_logger.log_exception(e, context="graph_refresh")

    t0 = loop.time()
    try:
        # Heartbeat-wrapped: the fused retriever embeds the query via Ollama,
        # which can stall when the autonomous researcher is also using Ollama.
        # Without a heartbeat the GUI freezes on "Searching vault..." with no
        # feedback. This pushes a "still alive / elapsed" pulse every 2s so
        # Sean always sees the backend is working, not hung.
        fused_result = await run_with_heartbeat(
            svc, websocket, "retrieving vault",
            svc.fused_retriever.retrieve, user_message, 5, 1)
        results = fused_result.get("results", []) if isinstance(fused_result, dict) else (fused_result or [])
    except Exception as e:
        session_logger.log_exception(e, context="fused_retriever.retrieve")
        # Degrade gracefully to flat vector search.
        try:
            results = await run_with_heartbeat(
                svc, websocket, "retrieving vault (fallback)",
                svc.vault_indexer.search, user_message, 5)
        except Exception:
            results = []
    session_logger.log("vault_search", {
        "query": user_message,
        "k": 5,
        "result_count": len(results),
        "duration_ms": (loop.time() - t0) * 1000,
        "retriever": "fused",
    })

    # RAG evaluation: log retrieval results for every query (cheap, always on).
    # Metrics are computed on-demand when ground truth is available.
    # See [[RAG-Evaluation-for-FUSED-Retrieval]].
    try:
        svc.rag_evaluator.log_retrieval(user_message, results, k=5)
    except Exception as e:
        session_logger.log("rag_eval_log_failed", {"error": str(e)})

    # Lazy-condenser touch tracking: record that each retrieved note was
    # queried.  Notes that cross the touch threshold (3+) AND are still long
    # get de-fluffed in the background after the answer is delivered — this
    # is the "de-fluff over time as pages are queried" behavior.  Never
    # raises; a failure here must not break the chat.
    retrieved_paths = []
    try:
        for r in results:
            fp = r.get("file_path") if isinstance(r, dict) else None
            if fp:
                retrieved_paths.append(fp)
                svc.lazy_condenser.note_touched(fp)
        # Persist the batched touch counts once per chat turn, not once per
        # retrieved note (each note_touched() only marks the dict dirty).
        svc.lazy_condenser.flush_touch_counts()
    except Exception as e:
        session_logger.log("lazy_condenser_touch_failed", {"error": str(e)})

    # Procedure context tracking: which procedural notes were in the vault
    # context for this turn? Used to log validation results against them.
    procedures_in_context = parse_procedures_from_results(results)
    if procedures_in_context:
        session_logger.log("procedures_in_context", {
            "procedures": procedures_in_context,
        })

    # Multi-resolution context: L2 MOC (bird's-eye) + L1 concept cards
    # (the thought highway — terse, hop-able) + L0 drill-down (full raw of
    # the single top seed only).  Replaces the old `build_graph_context`
    # content dump, which truncated every note to its first 2000 chars and
    # flooded the context with low-density detail.  Falls back to the legacy
    # builder if no L1 cards exist yet (pre-hierarchy vault regions).
    try:
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
    except Exception as e:
        session_logger.log_exception(e, context="build_abstract_context")
        context = build_graph_context(svc.vault_graph, results, user_message, k=5, depth=2)

    # Context budgeting: ensure the retrieved context fits within the
    # model's token budget. Truncates from the end (lowest-priority L0
    # drill-down detail) if the context would overflow the context window.
    # Pure deterministic -- no LLM calls. Graceful degradation: if the
    # budgeter fails, the original context is used unchanged.
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
    except Exception as e:
        session_logger.log("context_budget_failed", {"error": str(e)})

    # --- Step-gate runtime: compile-then-execute for procedural notes --- #
    # If any procedural notes were found in the retrieved context, try to
    # compile them into structured Procedure objects and route through the
    # step-gate runtime instead of the normal single-shot generation. The
    # step-gate runtime executes each step with an active frame (current
    # step first, full procedure overview, vault context) and validates
    # the output before advancing. This is the checkpointing pattern from
    # "Attention Deficits in Language Models" (arXiv 2602.19239) and the
    # full-program cursor from "Compile, Then Page" (arXiv 2607.11346).
    #
    # Falls through to normal generation if:
    #   - No procedures were found in context
    #   - Procedures don't compile (no structured steps)
    #   - The step-gate runtime crashes
    if procedures_in_context:
        try:
            compiled = []
            for proc_name in procedures_in_context:
                # Try to compile from the retrieved results' content first
                # (avoids a disk read), then fall back to disk.
                proc = None
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    fp = r.get("file_path", "")
                    if fp and Path(fp).stem == proc_name:
                        text = r.get("content") or r.get("snippet") or ""
                        if text:
                            proc = compile_from_text(proc_name, text)
                        break
                if proc is None:
                    # Fall back to disk
                    for r in results:
                        if not isinstance(r, dict):
                            continue
                        fp = r.get("file_path", "")
                        if fp and Path(fp).stem == proc_name:
                            proc = compile_procedure(fp)
                            break
                if proc and len(proc.steps) > 0:
                    compiled.append(proc)

            if compiled:
                # Execute the first compiled procedure through the step-gate
                # runtime. (Future: select the best procedure by relevance.)
                proc = compiled[0]
                session_logger.log("step_gate_start", {
                    "procedure": proc.name,
                    "steps": len(proc.steps),
                })

                # Progress callback: send step progress to the user
                async def _step_progress(step_num, total, output):
                    await svc.manager.send_personal_message(json.dumps({
                        "type": "status",
                        "content": f"Executing procedure step {step_num}/{total}...",
                    }), websocket, session_logger=session_logger)
                    if output:
                        await svc.manager.send_personal_message(json.dumps({
                            "type": "answer_chunk", "content": output + "\n\n",
                        }), websocket, session_logger=session_logger)

                exec_result = await execute_step_gate(
                    proc, context, svc.ollama_client,
                    session_logger=session_logger,
                    progress_callback=_step_progress,
                )

                # Log step-level results to procedure_tracker
                for sr in exec_result.steps:
                    try:
                        svc.procedure_tracker.log_step_result(
                            proc.name, sr.step_number,
                            sr.passed, sr.validation_error or "")
                    except Exception as e:
                        session_logger.log("step_gate_log_failed",
                                           {"error": str(e)})

                # Also log the overall procedure result
                try:
                    svc.procedure_tracker.log_result(
                        procedure=proc.name,
                        task="step_gate_execution",
                        validation_result="pass" if exec_result.overall_passed else "fail",
                        validation_tool="step_gate",
                        error_details="" if exec_result.overall_passed else
                            "; ".join(sr.validation_error or "" for sr in exec_result.steps if not sr.passed),
                    )
                except Exception as e:
                    session_logger.log("step_gate_proc_log_failed",
                                       {"error": str(e)})

                session_logger.log("step_gate_complete", {
                    "procedure": proc.name,
                    "steps_executed": len(exec_result.steps),
                    "overall_passed": exec_result.overall_passed,
                    "output_length": len(exec_result.final_output),
                })

                # Send the final output as the answer
                final_answer = exec_result.final_output
                await svc.manager.send_personal_message(json.dumps({
                    "type": "answer_done", "content": final_answer,
                }), websocket, session_logger=session_logger)
                session_logger.log("chat_end", {
                    "answer_length": len(final_answer),
                    "thinking_length": 0,
                    "tool_rounds": 0,
                    "step_gate": True,
                })

                # Persist this turn into conversation history
                try:
                    history = getattr(websocket, "conversation_history", None)
                    if history is not None and final_answer:
                        new_turns = history + [
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": final_answer},
                        ]
                        websocket.conversation_history = new_turns
                except Exception as e:
                    session_logger.log("history_persist_failed",
                                       {"error": str(e)})

                # Update goals
                try:
                    if len(user_message) > 15:
                        svc.identity.update_goals(
                            goal=user_message[:500],
                            next_step="(completed this turn)")
                except Exception as e:
                    session_logger.log("goals_update_failed",
                                       {"error": str(e)})

                # Regenerate self-model
                try:
                    activity = f"User asked: {user_message[:300]}\nAnswer: {final_answer[:500]}"
                    await loop.run_in_executor(
                        None, lambda: svc.identity.regenerate_self_model(activity))
                except Exception as e:
                    session_logger.log("self_model_regenerate_failed",
                                       {"error": str(e)})

                return  # Step-gate handled this turn; skip normal generation

        except Exception as e:
            session_logger.log("step_gate_failed", {
                "error": str(e),
                "fallback": "normal_generation",
            })
            # Fall through to normal generation

    # Inject the identity boot context so the agent wakes up coherent across
    # days regardless of which model is in the slot (IDENTITY + SELF_MODEL +
    # GOALS, delivered verbatim before the first turn — MIRROR/Letta pattern).
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
    except Exception:
        gaps = []
    gaps_summary = "\n".join(
        f"- [{g.get('kind')}] {g.get('topic')} (priority {g.get('priority', 0)})"
        for g in gaps[:10]) or "(none detected)"

    # Build the combined tool list: built-in vault tools + meta-tools (self-
    # improvement) + any agent-authored custom tools currently loaded.
    custom_schemas = svc.self_improver.custom_tool_schemas()
    custom_tool_names = [s["function"]["name"] for s in custom_schemas]
    all_tools = TOOL_DEFINITIONS + META_TOOL_DEFINITIONS + custom_schemas
    custom_tools_desc = "\n".join(
        f"- {s['function']['name']}: {s['function']['description'][:100]}"
        for s in custom_schemas) if custom_schemas else "(none yet)"

    system_prompt = (identity_context + "\n\n" +
                      build_system_prompt(context, autonomous_state, gaps_summary,
                                         custom_tools=custom_tools_desc,
                                         custom_tool_names=custom_tool_names))
    session_logger.log("prompt_built", {
        "system_prompt_length": len(system_prompt),
        "context_length": len(context),
        "gaps_reported": len(gaps),
        "custom_tools": len(custom_schemas),
        "total_tools": len(all_tools),
    })

    # Build the conversation for /api/chat using PERSISTENT per-session history.
    # This is the amnesia fix: prior turns (user + assistant + tool exchanges)
    # carry over within the same websocket session, so corrections and
    # context survive. History lives on websocket.conversation_history.
    # On the first turn it's empty; we rebuild the system prompt fresh each
    # turn (it carries live vault state) and prepend it.
    conversation = [{"role": "system", "content": system_prompt}]
    # Append the prior turns (no system prompt — that's rebuilt above each
    # turn so the agent always sees current vault state + gaps).
    conversation.extend(getattr(websocket, "conversation_history", []))
    # Add this turn's user message.
    conversation.append({"role": "user", "content": user_message})

    # Compact if the conversation is getting long (OpenHands Condenser pattern).
    # Prevents context overflow on long chats; keeps head + tail verbatim,
    # summarizes the middle. Now this actually has something to compact.
    if svc.compactor.should_compact(conversation):
        conversation = svc.compactor.compact(conversation)
        session_logger.log("context_compacted", {"messages": len(conversation)})

    await svc.manager.send_personal_message(json.dumps({"type": "status", "content": "Thinking..."}), websocket, session_logger=session_logger)

    # --- Agentic loop: reason → tool call → execute → feed back → repeat --- #
    # No cap on rounds/tool calls: the agent loops until it produces a final
    # answer (a turn with no tool calls) or the loop crashes.
    final_answer = ""
    thinking_text = ""
    total_chunks = 0
    t0 = loop.time()

    # Partial-answer crash protection: write the streamed-so-far answer to a
    # temp file so a crash mid-stream doesn't lose it. On normal completion
    # the file is deleted; on crash, it survives and the next session can
    # surface it ("You were answering 'X' when I crashed — here's what I had:").
    #
    # The partial dir lives OUTSIDE the vault (in the OS temp dir) so that
    # Obsidian's file-recovery core plugin — which snapshots every .md file
    # inside the vault — doesn't race the backend's delete and spam the
    # console with ENOENT errors. The old in-vault location
    # (vaultbot_backend/partials/) is cleaned up at startup.
    import hashlib
    import tempfile
    import time as _time
    partial_dir = Path(tempfile.gettempdir()) / "vaultbot_partials"
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_id = hashlib.md5((user_message + str(_time.time())).encode()).hexdigest()[:12]
    partial_path = partial_dir / f"partial_{partial_id}.md"
    write_partial(partial_path, user_message, "", "")  # create the file immediately

    try:
     round_idx = 0
     while True:
        # Stream the LLM response for this round.
        round_text = ""
        round_thinking = ""
        round_tool_calls = []
        chunk_count = 0
        try:
            def sync_stream():
                for chunk in svc.ollama_client.chat(conversation, tools=all_tools, stream=True):
                    yield chunk
            gen = sync_stream()
            round_t0 = loop.time()
            last_chunk_at = loop.time()
            while True:
                # Fetch the next chunk with a timeout so we can emit a
                # heartbeat while the model is silent (e.g. still loading the
                # model into memory, or in a long thinking pause before the
                # first token). This is what kills the black-box feeling:
                # even with zero output, the user sees "still thinking, 8s".
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
                        # Loop back: shield kept the task alive, retry the wait.
                    except asyncio.CancelledError:
                        # Interrupt (stop button / new message): close the
                        # Ollama generator so the backend thread stops pulling
                        # tokens, then re-raise so the outer handler exits.
                        gen.close()
                        raise
                if chunk.get("done") and not chunk.get("response") and not chunk.get("tool_calls"):
                    break
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
                    # Update the partial-answer file so a crash mid-stream
                    # preserves whatever was streamed so far.
                    write_partial(partial_path, user_message, final_answer + round_text, thinking_text)
                if tcs:
                    round_tool_calls.extend(tcs)
        except Exception as e:
            session_logger.log_exception(e, context="ollama_client.chat")
            await svc.manager.send_personal_message(json.dumps({"type": "error", "content": f"LLM error: {e}"}), websocket, session_logger=session_logger)
            return

        session_logger.log("agent_round", {
            "round": round_idx,
            "chunk_count": chunk_count,
            "text_length": len(round_text),
            "tool_calls": len(round_tool_calls),
        })

        # Append the assistant's turn to the conversation so the next round
        # sees the full history (including thinking for Qwen-style models).
        assistant_msg = {"role": "assistant", "content": round_text}
        if round_thinking:
            assistant_msg["thinking"] = round_thinking
        if round_tool_calls:
            assistant_msg["tool_calls"] = round_tool_calls
        conversation.append(assistant_msg)

        # No tool calls → the LLM produced a final answer. We're done.
        if not round_tool_calls:
            final_answer = round_text
            break

        # Accumulate non-final round text into final_answer so the partial
        # file captures all streamed text across rounds, not just the last.
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
            try:
                tool_result = await execute_agent_tool(
                    svc, tool_name, tool_args, session_logger, websocket)
            except Exception as e:
                session_logger.log_exception(e, context=f"tool_{tool_name}")
                tool_result = {"error": str(e)}
            # If the agent just created a tool, refresh the tool list so the
            # new tool is callable in the very next round.
            if tool_name == "tool_create":
                custom_schemas = svc.self_improver.custom_tool_schemas()
                all_tools = TOOL_DEFINITIONS + META_TOOL_DEFINITIONS + custom_schemas
            tool_duration = (loop.time() - t_tool0) * 1000
            session_logger.log("tool_call_result", {
                "tool": tool_name, "duration_ms": tool_duration,
                "result_keys": list(tool_result.keys()) if isinstance(tool_result, dict) else None,
            })

            # Procedure tracking: log validation results against procedures
            # that were in context for this turn. This is the deterministic
            # feedback loop -- no LLM judgment, just structured logging.
            if tool_name in ("vault_lint", "safe_write", "code_run"):
                try:
                    v_result, v_category, v_details = interpret_validation_result(
                        tool_name, tool_result)
                    proc_name = procedures_in_context[0] if procedures_in_context else "no_procedure"
                    svc.procedure_tracker.log_result(
                        procedure=proc_name,
                        task=tool_name,
                        validation_result=v_result,
                        validation_tool=tool_name,
                        error_details=v_details,
                        category=v_category,
                    )
                except Exception as e:
                    session_logger.log("procedure_tracking_failed", {"error": str(e)})
            await svc.manager.send_personal_message(json.dumps({
                "type": "tool_result", "tool": tool_name,
                "summary": tool_result_summary(tool_name, tool_result),
            }), websocket, session_logger=session_logger)

            conversation.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_result, default=str),
            })

        # Loop back: the LLM now sees the tool results and will produce
        # either another tool call or the final answer.
        round_idx += 1

    except Exception as e:
        # The whole agentic loop crashed — save whatever was streamed so far.
        session_logger.log_exception(e, context="handle_chat_agentic_loop")
        write_partial(partial_path, user_message, final_answer, thinking_text)
        session_logger.log("partial_answer_saved_on_crash", {
            "partial_path": str(partial_path),
            "answer_chars": len(final_answer),
        })
        raise
    finally:
        # If the answer completed normally, clean up the partial file.
        # If it didn't (crash, disconnect), the partial survives on disk.
        if final_answer and len(final_answer) > 50:
            try:
                if partial_path.exists():
                    partial_path.unlink()
            except Exception:
                pass

    session_logger.log("llm_generate", {
        "model": svc.ollama_client.llm_model,
        "stream": True,
        "total_chunks": total_chunks,
        "answer_length": len(final_answer),
        "thinking_length": len(thinking_text),
        "tool_rounds": round_idx + 1,
        "duration_ms": (loop.time() - t0) * 1000,
    })

    await svc.manager.send_personal_message(json.dumps({"type": "answer_done", "content": final_answer}), websocket, session_logger=session_logger)
    session_logger.log("chat_end", {
        "answer_length": len(final_answer),
        "thinking_length": len(thinking_text),
        "tool_rounds": round_idx + 1,
    })

    # Embedding-drift feedback (relevance feedback, LLM-free): nudge the
    # stored embeddings of retrieved notes toward (or away from) this query
    # based on whether the context was useful.  Signal heuristic:
    #   - If the agent produced a substantive answer (len > 50) on the
    #     FIRST round WITHOUT calling vault_research, the vault context was
    #     helpful → nudge the top retrieved note's embedding TOWARD the
    #     query (it ranks higher for similar queries next time).
    #   - If the agent's first move was to call vault_research (the vault
    #     was insufficient), the retrieved context was UNhelpful for this
    #     query → nudge the top retrieved note AWAY from the query.
    #   - A short answer (< 50 chars) is ambiguous → no signal.
    # This is the "scooch embeddings toward/away based on if the LLM says
    # it's helpful" behavior.  Zero extra LLM calls — the signal is derived
    # from the agent's own behavior.  Drift is capped + reset on rewrite
    # (see embedding_drift.py).
    if retrieved_paths:
        try:
            # did the agent research on round 0? (vault context unhelpful)
            researched_first = False
            # round_idx 0 + a research tool call in the first round.
            # We approximate: if final_answer is short AND round_idx > 0,
            # the agent looped through tools (likely research).  A cleaner
            # signal would track whether vault_research was called, but
            # this is LLM-free and good enough for drift seeding.
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
        except Exception as e:
            session_logger.log("drift_feedback_failed", {"error": str(e)})

    # Lazy de-fluff: after the answer is delivered, condense any retrieved
    # notes that have crossed the touch threshold (3+ queries) and are still
    # long.  Fire-and-forget so the user is never blocked — the condense LLM
    # call happens in the background.  Notes that are never queried are never
    # touched (zero wasted LLM calls).  A note that condenses gets its touch
    # counter reset so it isn't immediately re-condensed.
    if retrieved_paths:
        async def _run_lazy_condense_bg():
            try:
                summary = await loop.run_in_executor(
                    None, svc.lazy_condenser.condense_batch, retrieved_paths)
                if not summary.get("condensed"):
                    return
                session_logger.log("lazy_condense_done", summary)
                # Re-index condensed notes AND get the new embeddings back so
                # we can re-weave.  batch_add_files skips unchanged notes by
                # hash, so only the condensed ones cost an embedding call.
                # Detect which notes actually got condensed by checking for
                # the marker (the summary's details list uses stem names,
                # not full paths, so it's unreliable for path lookups).
                from lazy_condenser import CONDENSE_MARKER
                condensed_paths = []
                for fp in retrieved_paths:
                    try:
                        if CONDENSE_MARKER in Path(fp).read_text(
                                encoding="utf-8", errors="replace"):
                            condensed_paths.append(fp)
                    except Exception:
                        continue
                if not condensed_paths:
                    return
                _n, new_embs = await loop.run_in_executor(
                    None, svc.vault_indexer.batch_add_files,
                    condensed_paths, True)
                # --- Post-condense re-weave --- #
                # The condense LLM is told to keep all [[wikilinks]], but if
                # it drops a scaffolding sentence that carried a link, the
                # link goes with it.  Re-run the outbound linker on each
                # condensed note to restore any links whose concept is still
                # mentioned as plain text in the new body.  Idempotent (won't
                # double-wrap).  Also re-run the cross-book linker with the
                # NEW embeddings so the "## Related sections" block reflects
                # the condensed content, not the original.
                title_map = existing_note_titles(svc)
                for fp in condensed_paths:
                    try:
                        await loop.run_in_executor(
                            None, link_outbound, fp, title_map)
                    except Exception:
                        pass
                # Re-run cross-book linking on the condensed notes only.
                # source_keys = the condensed set (so a condensed note doesn't
                # cross-link to another condensed note from the same batch
                # incorrectly — though same-book exclusion is less precise
                # here since we don't have the full book path set; the
                # distance threshold still prevents weak matches).
                source_keys = {str(Path(fp).resolve()) for fp in condensed_paths}
                try:
                    cross = await loop.run_in_executor(
                        None, cross_link_textbooks, svc,
                        condensed_paths, new_embs, source_keys)
                    session_logger.log("post_condense_relink", {
                        "condensed": len(condensed_paths),
                        "cross_links": cross.get("cross_links_added", 0),
                    })
                except Exception as e:
                    session_logger.log("post_condense_crosslink_failed",
                                       {"error": str(e)})
                # --- L1 concept-card lazy refine (rehearsal-gated) --- #
                # Cards retrieved 3+ times get a one-shot LLM rewrite into a
                # tight semantic summary (same rehearsal contract as the L0
                # condenser).  When an L0 section is condensed, also refresh
                # its card so the card reflects the new terse content.  Zero
                # LLM calls for cards that haven't earned it.
                try:
                    from concept_card import (
                        build_card_for,
                        card_path_for,
                        needs_refine,
                        refine_card,
                    )
                    # First: refresh cards for any condensed L0 sections so
                    # the card sketch reflects the new body (unless the card
                    # was already LLM-refined, which is sticky).  Also RESET
                    # the embedding drift for any note whose content changed
                    # (condense or refine) — the old drift was earned against
                    # content that no longer exists, so keeping it would
                    # mislead retrieval.
                    for fp in condensed_paths:
                        card = card_path_for(fp)
                        if card.exists():
                            # Re-build the extractive sketch only if not refined.
                            try:
                                old = card.read_text(
                                    encoding="utf-8", errors="replace")
                                from concept_card import REFINED_MARKER
                                if REFINED_MARKER not in old:
                                    build_card_for(fp, vault_graph=svc.vault_graph)
                            except Exception:
                                pass
                        # Drift reset: content changed, old drift is invalid.
                        try:
                            svc.embedding_drift.reset(fp)
                            if card.exists():
                                svc.embedding_drift.reset(str(card))
                        except Exception:
                            pass
                    # Then: LLM-refine any retrieved cards that have crossed
                    # the rehearsal threshold.  Uses the touch counter the
                    # lazy_condenser already maintains.
                    refined = 0
                    for fp in retrieved_paths:
                        card = card_path_for(fp)
                        if not card.exists():
                            continue
                        try:
                            tc = svc.lazy_condenser.touch_counts.get(
                                str(Path(card).resolve()), 0)
                        except Exception:
                            tc = 0
                        if needs_refine(card, tc):
                            r = await loop.run_in_executor(
                                None, refine_card, card, svc.ollama_client, None)
                            if r.get("refined"):
                                refined += 1
                                # re-index the refined card
                                await loop.run_in_executor(
                                    None, svc.vault_indexer.batch_add_files,
                                    [str(card)], False)
                                # Drift reset: the card's content changed
                                # (extractive → LLM summary), so the old
                                # drift is invalid.
                                try:
                                    svc.embedding_drift.reset(str(card))
                                except Exception:
                                    pass
                    if refined:
                        session_logger.log("card_refine_done",
                                           {"refined": refined})
                except Exception as e:
                    session_logger.log("card_refine_failed",
                                       {"error": str(e)})
            except Exception as e:
                session_logger.log("lazy_condense_bg_failed", {"error": str(e)})
        asyncio.create_task(_run_lazy_condense_bg())

    # Persist this turn into the per-session history so the NEXT message has
    # context. We save the system-prompt-stripped conversation (the system
    # prompt is rebuilt fresh each turn) — i.e. everything after the initial
    # system message: the user turn, all assistant + tool rounds, and the
    # final assistant answer. This is what gets prepended next turn.
    try:
        history = getattr(websocket, "conversation_history", None)
        if history is not None and final_answer:
            # The conversation list is [system, ...history, user, assistant,
            # tool, assistant, ...]. Strip the leading system message and
            # everything we just added (user msg onward) is the new history.
            new_turns = [m for m in conversation if m.get("role") != "system"]
            websocket.conversation_history = new_turns
            session_logger.log("history_persisted", {
                "turns": len(new_turns),
                "history_chars": sum(len(str(m.get("content", ""))) for m in new_turns),
            })
    except Exception as e:
        session_logger.log("history_persist_failed", {"error": str(e)})

    # Save a chat note if the answer is substantive
    if len(final_answer) > 100:
        try:
            note_path = await loop.run_in_executor(None, svc.note_creator.create_note_from_chat, user_message, final_answer, thinking_text)
            session_logger.log("chat_note_created", {"note_path": note_path})
        except Exception as e:
            session_logger.log_exception(e, context="note_creator.create_note_from_chat")
            print(f"Error creating chat note: {e}")

    # Keep GOALS.md current: if the user's message looks like a task/request
    # (not a casual greeting), update the active goal so the agent remembers
    # what it's working on across restarts. This is the Generative Agents
    # plan-persistence pattern — the goal lives in a file, not in context.
    try:
        if len(user_message) > 15 and not user_message.lower().startswith(("hi", "hey", "sup", "hello", "yo")):
            # Simple heuristic: the user's message IS the current goal.
            # The self-model already captures what happened; GOALS captures
            # what's active. If the answer completed the request, the goal
            # clears next turn; if it's ongoing (e.g. multi-step), it persists.
            svc.identity.update_goals(
                goal=user_message[:500],
                next_step="(in progress)" if len(final_answer) < 200 else "(completed this turn)"
            )
            session_logger.log("goals_updated", {"goal": user_message[:100]})
    except Exception as e:
        session_logger.log("goals_update_failed", {"error": str(e)})

    # Close the MIRROR loop: regenerate the bounded self-model from this
    # turn's activity so the agent consolidates its reasoning into a durable
    # first-person narrative that survives context compaction and model swaps.
    # This is the +9.3% vs +2.4% finding (MIRROR arXiv:2506.00430): the value
    # of thinking lies in maintaining its outputs across time, not the act of
    # thinking itself.
    try:
        activity = f"User asked: {user_message[:300]}\nAnswer: {final_answer[:500]}"
        await loop.run_in_executor(None, lambda: svc.identity.regenerate_self_model(activity))
    except Exception as e:
        session_logger.log("self_model_regenerate_failed", {"error": str(e)})

    # Pattern extraction: check for new consolidation gaps after each chat.
    # This is the episodic -> semantic consolidation trigger. The pattern
    # extractor scans chat logs for recurring topics, correction patterns,
    # and self-model drift. Gaps are logged so the autonomous researcher
    # can consolidate them into semantic knowledge notes.
    # Pure deterministic -- no LLM calls. See [[Semantic-Consolidation-Architecture]].
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
    except Exception as e:
        session_logger.log("pattern_extraction_failed", {"error": str(e)})


async def execute_agent_tool(svc: Services, tool_name: str, args: dict[str, Any],
                             session_logger, websocket: WebSocket | None = None) -> dict[str, Any]:
    """Execute one tool call from the chat LLM. Runs in the async context.

    `websocket` is passed so long-running tools (vault_research) can push
    live progress events to the UI instead of going silent for 30-60s.
    """
    # Module-level imports from chat_helpers, weaving — no longer deferred
    # from main (circular dependency eliminated).
    loop = asyncio.get_event_loop()

    if tool_name == "vault_research":
        topic = (args.get("topic") or "").strip()
        depth = args.get("depth", "deep")
        if not topic:
            return {"error": "missing topic"}
        if depth == "quick":
            svc.research_engine.max_rounds = 1
            svc.research_engine.max_follow_ups = 0

        # Live progress: the research engine calls back from a worker thread
        # at each stage. We marshal those into websocket sends on the loop so
        # the UI shows "round 2/4, 12 sources…" instead of a black box.
        prev_cb = svc.research_engine.progress_callback
        if websocket is not None:
            def _progress_cb(stage: str, detail: dict):
                try:
                    asyncio.run_coroutine_threadsafe(
                        send_progress(svc, websocket, stage, detail), loop)
                except Exception:
                    pass
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
                md = svc.research_engine.synthesize_note_markdown(report, summary)
                try:
                    Path(note_path).write_text(md, encoding="utf-8")
                except Exception:
                    pass
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
            except Exception as e:
                session_logger.log("amem_evolve_failed", {"error": str(e)})
        return report

    if tool_name == "vault_search":
        query = args.get("query", "")
        k = int(args.get("k", 5))
        results = await loop.run_in_executor(None, svc.vault_indexer.search, query, k)
        return {"query": query, "results": [
            {"file_path": r.get("file_path"), "content": r.get("content", "")[:1200],
             "score": r.get("score")} for r in results
        ]}

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

    if tool_name == "capability_audit":
        return await loop.run_in_executor(None, lambda: svc.self_improver.capability_audit(
            args.get("task", "")))

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
                    except Exception as e:
                        session_logger.log("textbook_weave_bg_failed",
                                           {"error": str(e)})
                asyncio.create_task(_run_weave_bg())
        return result

    return {"error": f"unknown tool: {tool_name}"}
