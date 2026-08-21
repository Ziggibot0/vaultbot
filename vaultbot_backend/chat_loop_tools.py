"""Per-tool-call execution for one agentic-loop round.

Extracted from ``chat_agentic_loop.py`` — ``execute_round_tools`` runs the
``for tc in round_tool_calls`` loop: it dispatches each tool, tracks
seen-content, dedups vault_search results, escalates go-find-out, caps and
appends tool results, and records the findings ledger. It mutates ``st`` and
may rebuild ``all_tools``/``custom_schemas`` on ``tool_create``, so it returns
the (possibly updated) ``(all_tools, custom_schemas)``.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_tools import build_tool_list
from chat_context import dedup_seen_results as _dedup_seen_results
from chat_helpers import (
    notify_console_failure,
    tool_result_summary,
    truncate_tool_result,
)
from chat_loop_state import TurnState
from chat_preflight import check_cancelled as _check_cancelled
from chat_tool_dispatch import execute_agent_tool
from config import TUNABLES
from procedure_tracker import interpret_validation_result
from services import Services
from working_memory import TaskList


def is_malformed_tool_name(tool_name: str) -> bool:
    """True if ``tool_name`` is not a valid tool identifier (issue #130).

    Under context bloat the model can emit a "tool name" that is actually
    prior tool-result text (a ~2000-char code_read JSON smashed together
    with prose). A valid tool name is short, has no whitespace, and
    contains no JSON braces or colons. This is a pure function so it can
    be unit-tested directly (see tests/test_chat_loop_tools.py).
    """
    if not tool_name:
        return True
    if len(tool_name) > 64:
        return True
    if any(ch.isspace() for ch in tool_name):
        return True
    if "{" in tool_name or "}" in tool_name or ":" in tool_name:
        return True
    return False


async def execute_round_tools(
    svc: Services,
    websocket,
    session_logger,
    loop,
    user_message: str,
    conversation: list,
    round_tool_calls: list,
    st: TurnState,
    all_tools: list,
    custom_schemas: list,
    wm: TaskList,
    procedures_in_context: list,
) -> tuple[list, list]:
    """Execute each tool call and feed results back as tool-role messages.

    Returns the (possibly updated) (all_tools, custom_schemas) — rebuilt when
    the agent creates a new tool via ``tool_create``.
    """
    # Execute each tool call and feed results back as tool-role messages.
    for tc in round_tool_calls:
        _check_cancelled(websocket)
        fn = tc.get("function", {})
        tool_name = fn.get("name", "")
        tool_args_raw = fn.get("arguments", "{}")

        # --- Malformed tool-name guard (issue #130) --------------------
        # Under context bloat the model can emit a "tool name" that is
        # actually prior tool-result text (a ~2000-char code_read JSON
        # smashed together with prose). Dispatching that returns a bare
        # "unknown tool: <garbled>" and echoes the whole blob back into
        # context, poisoning the next round. Detect it here: a valid tool
        # name is short, has no whitespace, and contains no JSON braces.
        # On a malformed name, feed back a SHORT, actionable error (never
        # the garbled string) so the model can recover instead of looping.
        if is_malformed_tool_name(tool_name):
            session_logger.log(
                "malformed_tool_name",
                {
                    "round": st.round_idx,
                    "name_len": len(tool_name),
                    "name_preview": tool_name[:80],
                },
            )
            tool_result = {
                "error": (
                    "malformed tool call: the tool name was not a valid "
                    "tool identifier (it may have been corrupted by context "
                    "bloat). Re-emit a clean tool call with a short, "
                    "single-word tool name from the available tools list."
                )
            }
            # Feed the short error back and skip dispatch entirely.
            await svc.manager.send_personal_message(
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool": "<malformed>",
                        "summary": "malformed tool call rejected",
                    }
                ),
                websocket,
                session_logger=session_logger,
            )
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", "malformed"),
                    "tool_name": "<malformed>",
                    "content": json.dumps(tool_result, default=str),
                }
            )
            st._turn_tool_history.append(
                {
                    "round": st.round_idx,
                    "tool": "<malformed>",
                    "result_summary": "malformed tool call rejected",
                }
            )
            continue
        try:
            tool_args = (
                json.loads(tool_args_raw)
                if isinstance(tool_args_raw, str)
                else tool_args_raw
            )
        except json.JSONDecodeError:
            tool_args = {}
        tool_call_id = tc.get("id", tool_name)
        # Allocate a session-scoped correlation ID so the reader can
        # match this tool_call_requested to its tool_call_result
        # deterministically. See issue #86, Fix #5.
        _log_call_id = session_logger.next_call_id()

        # Track the last vault_search query for go-find-out: when the
        # vault has no answer and the harness auto-triggers web
        # research, the search query is a focused research topic (not
        # the raw user message, which is conversational and produces
        # zero search hits). See [[How-to-Fix-Research-Engine-Returning-Garbage]].
        if tool_name == "vault_search":
            st._last_search_query = tool_args.get("query", "")

        await svc.manager.send_personal_message(
            json.dumps({"type": "tool_call", "tool": tool_name, "args": tool_args}),
            websocket,
            session_logger=session_logger,
        )
        session_logger.log(
            "tool_call_requested",
            {
                "call_id": _log_call_id,
                "tool": tool_name,
                "args": tool_args,
                "round": st.round_idx,
            },
        )

        # --- code_read whole-file auto-expand ---
        # If the model calls code_read on a file it ALREADY read this
        # turn (tracked in st._seen_content), expand to the whole file.
        # This collapses the 5-10 chunked 80-line reads into 1 call,
        # which is how Copilot reads (whole file, large range). The
        # model self-chunks because it sees total_lines and gets
        # anxious; auto-expand removes the anxiety by giving it the
        # full file on the repeat. First read stays as-is (the model
        # chose that range for a reason).
        if tool_name == "code_read":
            _cr_fp = tool_args.get("file_path", "")
            _cr_seen = st._seen_content.get(_cr_fp)
            if _cr_seen and _cr_fp:
                # Already saw this file → give the whole file.
                tool_args["start_line"] = 1
                tool_args["end_line"] = 0  # 0 = whole file
                session_logger.log(
                    "code_read_auto_expand",
                    {
                        "round": st.round_idx,
                        "file_path": _cr_fp,
                        "prev_source": _cr_seen.get("source", "?"),
                    },
                )

        t_tool0 = loop.time()
        session_logger.log(
            "tool_exec_enter",
            {
                "tool": tool_name,
                "round": st.round_idx,
                "t_ms": t_tool0 * 1000,
            },
        )
        try:
            tool_result = await execute_agent_tool(
                svc,
                tool_name,
                tool_args,
                session_logger,
                websocket,
                user_message=user_message,
            )
        except Exception as e:  # noqa: BLE001
            session_logger.log_exception(e, context=f"tool_{tool_name}")
            tool_result = {"error": str(e)}
            # Immediately report the tool crash to the console so the
            # user sees it in red, not buried in a tool_result summary
            # that the plugin renders with a green ✓. This is the
            # "any failure of any kind is immediately reported" rule.
            await notify_console_failure(
                svc,
                websocket,
                f"tool {tool_name} crashed: {e}",
                context="tool_exec",
            )

        _check_cancelled(websocket)

        # --- Seen-content tracking for vault_search & code_read ----
        # Track which files the model has seen this turn so we can
        # dedup future vault_search results and break search loops.
        if tool_name == "vault_search" and isinstance(tool_result, dict):
            for _r in tool_result.get("results", []):
                _fp = _r.get("file_path", "")
                if _fp:
                    st._seen_content[_fp] = {
                        "source": "vault_search",
                        "lines": None,
                        "round": st.round_idx,
                    }
        elif (
            tool_name in ("code_read", "vault_read_note")
            and isinstance(tool_result, dict)
            and not tool_result.get("error")
        ):
            _fp = tool_result.get("file_path", "")
            _sl = tool_result.get("start_line", 1)
            _el = tool_result.get("end_line", 0)
            _tl = tool_result.get("total_lines", 0)
            if _fp:
                # If the read covered the whole file, mark it fully seen.
                # Otherwise track the specific line range.
                _full = _sl <= 1 and (_el <= 0 or _el >= _tl)
                st._seen_content[_fp] = {
                    "source": tool_name,
                    "lines": None if _full else (_sl, _el),
                    "round": st.round_idx,
                }

        # --- Closed-set citation: register tool-retrieved notes --------
        # When the model calls vault_search / vault_read_note mid-loop,
        # those notes become valid citation targets. Update the
        # allowed-citations set so the grounding gate accepts them. This
        # makes the closed set dynamic — the model can cite notes it
        # retrieved on its own, not just the preflight ones.
        try:
            from citation_gate import add_citation_target

            if tool_name == "vault_search" and isinstance(tool_result, dict):
                for _r in tool_result.get("results", []):
                    _fp = _r.get("file_path", "")
                    if _fp:
                        add_citation_target(
                            st._allowed_citations,
                            _fp,
                            _r.get("content", ""),
                        )
            elif (
                tool_name == "vault_read_note"
                and isinstance(tool_result, dict)
                and not tool_result.get("error")
            ):
                _fp = tool_result.get("file_path", "")
                if _fp:
                    add_citation_target(
                        st._allowed_citations,
                        _fp,
                        tool_result.get("content", ""),
                    )
        except Exception:  # noqa: BLE001 — best-effort, never break the tool
            pass

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
                _raw_results, st._seen_content
            )
            if _already_seen:
                _seen_names = ", ".join(
                    Path(o["file_path"]).stem for o in _already_seen[:10]
                )
                session_logger.log(
                    "search_results_deduped",
                    {
                        "round": st.round_idx,
                        "raw_count": len(_raw_results),
                        "already_seen": len(_already_seen),
                        "new_results": len(_annotated) - len(_already_seen),
                        "seen_files": [
                            Path(o["file_path"]).stem for o in _already_seen[:10]
                        ],
                    },
                )
                tool_result["results"] = _annotated
                _new_count = len(_annotated) - len(_already_seen)
                if _new_count == 0:
                    # ALL results were already seen — increment the
                    # go-find-out escalation counter.
                    st._consecutive_all_seen += 1
                    _go_find_out_threshold = int(
                        os.getenv("VAULTBOT_GO_FIND_OUT_THRESHOLD", "3")
                    )
                    if (
                        st._consecutive_all_seen >= _go_find_out_threshold
                        and not st._go_find_out_fired
                    ):
                        # GO FIND OUT: the vault doesn't have what the
                        # model needs. Auto-trigger web research on the
                        # user's original question and inject the result
                        # as a tool result so the model gets new info.
                        st._go_find_out_fired = True
                        # Use the last vault_search query as the
                        # research topic, NOT the raw user message.
                        # The user message is conversational ("dude
                        # stop relying on model weights...") — search
                        # engines return nothing for that and the
                        # relevance gate filters out what little
                        # comes back, producing zero-source research.
                        # The model's own search query is a proper
                        # research topic that the engines can handle.
                        _research_topic = st._last_search_query or user_message[:200]
                        session_logger.log(
                            "go_find_out_triggered",
                            {
                                "round": st.round_idx,
                                "consecutive_all_seen": st._consecutive_all_seen,
                                "query": _research_topic[:100],
                                "source": "last_search_query"
                                if st._last_search_query
                                else "user_message",
                            },
                        )
                        await svc.manager.send_personal_message(
                            json.dumps(
                                {
                                    "type": "status",
                                    "content": (
                                        "Vault doesn't have enough — "
                                        "researching on the web..."
                                    ),
                                }
                            ),
                            websocket,
                            session_logger=session_logger,
                        )
                        try:
                            _research_result = await execute_agent_tool(
                                svc,
                                "vault_research",
                                {
                                    "topic": _research_topic,
                                    "depth": "quick",
                                },
                                session_logger,
                                websocket,
                                user_message=user_message,
                            )
                            # Build a compact summary of the research
                            # result for the system message.
                            _research_brief = ""
                            if isinstance(_research_result, dict):
                                _rb = _research_result.get("synthesis_brief", "")
                                _kf = _research_result.get("key_facts", "")
                                _np = _research_result.get("note_path", "")
                                _parts = []
                                if _rb:
                                    _parts.append(_rb[:2000])
                                if _kf:
                                    _parts.append(f"Key facts:\n{_kf}")
                                if _np:
                                    _parts.append(
                                        f"A permanent note was created at {_np}."
                                    )
                                _research_brief = "\n\n".join(_parts)
                            # Store the system message for injection
                            # after the tool results are appended.
                            st._go_find_out_msg = (
                                f"# GO-FIND-OUT: Web research "
                                f"completed automatically\n"
                                f"The vault did not contain enough "
                                f"information for this question after "
                                f"{st._consecutive_all_seen} searches. "
                                f"I automatically researched it on the "
                                f"web. Here are the results:\n\n"
                                f"{_research_brief or '(no summary available)'}\n\n"
                                f"Use these research results to answer "
                                f"the user's question NOW. Do NOT call "
                                f"vault_search again. Do NOT look for "
                                f"procedures. You have the information "
                                f"— write your answer."
                            )
                            # Also keep the tool result for the model
                            # to see in the tool response.
                            tool_result = {
                                "go_find_out": True,
                                "message": (
                                    "Web research completed "
                                    "automatically. See the system "
                                    "message for results. Use them "
                                    "to answer now — do NOT search "
                                    "again."
                                ),
                                "research": _research_result,
                            }
                        except Exception as e:  # noqa: BLE001
                            session_logger.log("go_find_out_failed", {"error": str(e)})
                            tool_result["message"] = (
                                f"All search results are files you "
                                f"already have, and auto-research "
                                f"failed ({e}). Answer from what you "
                                f"already have — do NOT search again."
                            )
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
                            f"Do NOT call vault_search again."
                        )
                else:
                    # Some new results — reset the counter.
                    st._consecutive_all_seen = 0
        session_logger.log(
            "tool_exec_exit",
            {
                "tool": tool_name,
                "round": st.round_idx,
                "duration_ms": (loop.time() - t_tool0) * 1000,
            },
        )
        # If the agent just created a tool, refresh the tool list.
        if tool_name == "tool_create":
            custom_schemas = svc.self_improver.custom_tool_schemas()
            all_tools = build_tool_list(
                user_message,
                wm.render_for_prompt() if wm else "",
                custom_schemas,
            )
        tool_duration = (loop.time() - t_tool0) * 1000
        session_logger.log(
            "tool_call_result",
            {
                "call_id": _log_call_id,
                "tool": tool_name,
                "round": st.round_idx,
                "duration_ms": tool_duration,
                "result_keys": list(tool_result.keys())
                if isinstance(tool_result, dict)
                else None,
            },
        )

        # Procedure tracking: log validation results.
        if tool_name in ("vault_lint", "safe_write", "code_run"):
            try:
                v_result, v_category, v_details = interpret_validation_result(
                    tool_name, tool_result
                )
                proc_name = (
                    procedures_in_context[0]
                    if procedures_in_context
                    else "no_procedure"
                )
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
                await notify_console_failure(
                    svc,
                    websocket,
                    f"procedure tracking failed: {e}",
                    context="procedure_tracker",
                )
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": tool_result_summary(tool_name, tool_result),
                }
            ),
            websocket,
            session_logger=session_logger,
        )

        # Cap the tool result before appending.
        # ALL tool results are bounded. code_read / vault_read_note
        # get a very generous cap (read_result_cap, default 120K chars
        # ≈ 30K tokens) so the model sees the WHOLE file in virtually
        # all cases — only truly enormous files (500K+ chars) that
        # would actually hurt the model are truncated. Other tool
        # results get the standard cap (10K chars). The hard token
        # cap (_enforce_token_cap) is the final guarantee, and it
        # also exempts read tools from stubbing.
        _READ_CAP = int(
            os.getenv("VAULTBOT_READ_RESULT_CAP", str(TUNABLES.read_result_cap))
        )
        # Read tools get the generous cap so the model sees the WHOLE
        # content. github_issues is a read tool too (issue bodies +
        # comment threads must be fully readable to be reasoned about —
        # see issue #128); it was previously capped at the standard 10K
        # chars, cutting issue bodies off mid-sentence.
        if tool_name in ("code_read", "vault_read_note", "github_issues"):
            capped_result = truncate_tool_result(tool_result, max_chars=_READ_CAP)
        else:
            capped_result = truncate_tool_result(tool_result)
        # All models get the SAME treatment: raw tool results,
        # bounded only by truncate_tool_result for context-window
        # safety. No per-model heuristics (no thinking-model
        # digest, no name sniffing) — every model sees the same
        # content. code_read / vault_read_note are never digested
        # or structurally summarized; the raw content lands in the
        # conversation as-is.
        conversation.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "content": json.dumps(capped_result, default=str),
            }
        )
        # Record for the chat-loop checkpoint.
        st._turn_tool_history.append(
            {
                "round": st.round_idx,
                "tool": tool_name,
                "result_summary": (tool_result_summary(tool_name, tool_result) or "")[
                    :200
                ],
            }
        )

    return all_tools, custom_schemas
