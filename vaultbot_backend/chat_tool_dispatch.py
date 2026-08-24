"""Tool dispatch for the agentic chat loop.

Extracted from ``chat_handler.py`` — the ``execute_agent_tool`` function
that routes tool calls from the chat LLM to their handlers: vault_search,
vault_read_note, vault_research, code_read, code_run, safe_write,
execute_procedure, plan_task, update_task, custom tools, etc.

Self-contained: takes ``(svc, tool_name, args, session_logger, websocket,
user_message)`` and returns a result dict. No dependency on the loop logic.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from chat_helpers import (
    notify_console_failure,
    notify_problem,
    run_with_heartbeat,
    send_progress,
)
from chat_preflight import check_cancelled, dispatch_procedure_core
from fastapi import WebSocket
from services import Services
from weaving import weave_textbook_notes
from working_memory import TaskList

# Fire-and-forget task registry: keeps strong references to background
# tasks so they aren't garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


async def execute_agent_tool(
    svc: Services,
    tool_name: str,
    args: dict[str, Any],
    session_logger,
    websocket: WebSocket | None = None,
    user_message: str = "",
    conversation: list | None = None,
) -> dict[str, Any]:
    """Execute one tool call from the chat LLM. Runs in the async context.

    `websocket` is passed so long-running tools (vault_research) can push
    live progress events to the UI instead of going silent for 30-60s.

    `conversation` is the LIVE in-memory conversation list for the current
    turn (the same list the agentic loop appends to). It is passed so the
    ``backend_restart`` tool can force-save the ACTUAL current thread before
    restarting — ``websocket.conversation_history`` is only synced to the
    live list at the END of a turn, so a mid-turn restart would otherwise
    persist only the stale pre-turn history and lose the whole live thread.
    """
    # Module-level imports from chat_helpers, weaving — no longer deferred
    # from main (circular dependency eliminated).
    loop = asyncio.get_event_loop()
    session_logger.log(
        "execute_agent_tool_body_start",
        {
            "tool": tool_name,
            "t_ms": loop.time() * 1000,
        },
    )

    # Check cancel flag before executing any tool — the user may have
    # pressed stop while the loop was between rounds.
    if websocket is not None:
        check_cancelled(websocket)

    # ── Safe Mode gate ─────────────────────────────────────────────────
    # In Safe Mode (default), dangerous tools (code_write, code_run,
    # tool_create, vault_delete, etc.) are blocked. The user must explicitly
    # opt into Developer Mode to enable self-modification. See safe_mode.py.
    from safe_mode import blocked_tool_message, is_tool_allowed

    if not is_tool_allowed(tool_name):
        msg = blocked_tool_message(tool_name)
        session_logger.log("safe_mode_blocked", {"tool": tool_name})
        return {"error": msg, "safe_mode_blocked": True}

    if tool_name == "vault_research":
        # ── Web research gate ──────────────────────────────────────────
        # If VAULTBOT_ALLOW_WEB_RESEARCH is disabled, block web research.
        # The agent can still search the vault and read notes, but cannot
        # fetch new content from the internet.
        if os.environ.get("VAULTBOT_ALLOW_WEB_RESEARCH", "true").strip().lower() in (
            "0",
            "false",
            "off",
            "no",
        ):
            return {
                "error": (
                    "Web research is disabled. Set 'Allow web research' in "
                    "VaultBot Settings to enable it."
                )
            }

        topic = (args.get("topic") or "").strip()
        depth = args.get("depth", "deep")
        # Source-authority constraints (issue #133): the caller may require
        # authoritative-only sources ("ONLY Google official docs") via an
        # allowlist, or block known-low-quality domains via a denylist.
        source_allowlist = args.get("source_allowlist")
        source_denylist = args.get("source_denylist")
        if not topic:
            return {"error": "missing topic"}

        # SUBAGENT CONTEXT ISOLATION (Copilot runSubagent / Claude subagent
        # pattern): run the full dig + note creation in a SEPARATE PROCESS so
        # the verbose work (1900+ source-rejection events, scrapes, a 50K
        # synthesis) never enters the orchestrator's conversation. The
        # subprocess prints ONLY a compact JSON brief to stdout; the chat
        # loop gets one bounded tool result, not a flood. The full synthesis
        # stays on disk in the created note — re-readable via vault_search /
        # web_read_source. See subagent.py.
        #
        # Fallback: if VAULTBOT_SUBAGENT=off (or the import fails), the
        # in-process path runs below. It still distills the report to a
        # bounded brief before returning, so the conversation never balloons
        # either way. The subagent path is strictly safer (hard process
        # isolation); the in-process path is the safety net.
        try:
            from subagent import run_research_subagent, subagent_enabled

            _use_subagent = subagent_enabled()
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            _use_subagent = False

        if _use_subagent:
            session_logger.log("subagent_research_invoked", {"topic": topic[:80]})
            # Emit heartbeats while the subprocess runs — the child can't send
            # websocket events (it's a separate process), so the orchestrator
            # keeps the UI alive with the existing run_with_heartbeat pattern.
            t_sub = loop.time()
            try:
                brief = await run_with_heartbeat(
                    svc,
                    websocket,
                    f"research{topic[:40]}",
                    run_research_subagent,
                    topic,
                    depth,
                    session_logger,
                    source_allowlist,
                    source_denylist,
                )
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log_exception(e, context="subagent_research")
                brief = {
                    "status": "error",
                    "error": f"subagent research failed: {e}",
                    "subagent": True,
                }
            # The subagent already created + indexed the note. Refresh the
            # orchestrator's in-memory graph so subsequent rounds see it
            # (the child's indexer is its own instance).
            try:
                await loop.run_in_executor(None, svc.vault_graph.refresh)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log(
                    "post_subagent_graph_refresh_failed", {"error": str(e)}
                )
            session_logger.log(
                "subagent_research_complete",
                {
                    "duration_ms": int((loop.time() - t_sub) * 1000),
                    "status": brief.get("status"),
                    "source_count": brief.get("source_count", 0),
                    "note_path": brief.get("note_path"),
                },
            )
            # Normalize: an empty-status brief becomes an error so the model
            # doesn't treat a no-sources dig as a real result.
            if brief.get("status") == "empty":
                brief["error"] = brief.get("error", "no web sources found")
            return brief

        # --- In-process fallback (VAULTBOT_SUBAGENT=off or import fail) ---
        # Kept as a safety net. Still distills the report to a bounded brief
        # so the conversation never balloons. The subagent path above is
        # preferred (hard isolation); this path shares the loop.
        if depth == "quick":
            svc.research_engine.max_rounds = 1
            svc.research_engine.max_follow_ups = 0

        prev_cb = svc.research_engine.progress_callback
        if websocket is not None:

            def _progress_cb(stage: str, detail: dict):
                try:
                    asyncio.run_coroutine_threadsafe(
                        send_progress(svc, websocket, stage, detail), loop
                    )
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    session_logger.log("tool_progress_cb_failed", {"error": str(e)})

            svc.research_engine.progress_callback = _progress_cb

        t_research = loop.time()
        try:
            report = await run_with_heartbeat(
                svc,
                websocket,
                f"research{topic[:40]}",
                svc.research_engine.research,
                topic,
                None,  # llm_client (in-process path uses the engine's own)
                None,  # vault_note_titles
                source_allowlist,
                source_denylist,
            )
        finally:
            svc.research_engine.max_rounds = int(
                os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4")
            )
            svc.research_engine.max_follow_ups = int(
                os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3")
            )
            svc.research_engine.progress_callback = prev_cb
            session_logger.log(
                "agent_research_done",
                {
                    "duration_ms": (loop.time() - t_research) * 1000,
                    "source_count": report.get("source_count", 0)
                    if isinstance(report, dict)
                    else 0,
                },
            )
        # Persist a linked note so the research becomes vault knowledge.
        if report.get("source_count") and report.get("synthesis"):
            try:
                summary = (
                    f"Research into '{topic}' ({report['source_count']} "
                    f"sources, {report['synthesis_facts']} facts)."
                )
                await send_progress(svc, websocket, "writing_note", {"topic": topic})
                note_path = await run_with_heartbeat(
                    svc,
                    websocket,
                    "writing_note",
                    svc.note_creator.create_note_from_research,
                    topic,
                    report["synthesis"],
                    summary,
                )
                if report.get("llm_synthesized"):
                    # LLM synthesis already produced a structured note with
                    # frontmatter, H2 prose sections, wikilinks, and Sources.
                    # Route it through inject_schema to sanitize placeholder
                    # summaries, dedupe tags, and fill any missing required
                    # fields before writing to disk.
                    try:
                        from note_schema import inject_schema

                        _sanitized = inject_schema(
                            report["synthesis"],
                            f"vaultbot-stuff/Knowledge/Research/{topic}.md",
                            force_type="research",
                        )
                        Path(note_path).write_text(_sanitized, encoding="utf-8")
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log(
                            "research_note_write_failed",
                            {"path": note_path, "error": str(e)},
                        )
                else:
                    # Extractive fallback: wrap in markdown, then try LLM
                    # structuring (ONE call) for frontmatter + H2 sections.
                    md = svc.research_engine.synthesize_note_markdown(report, summary)
                    Path(note_path).write_text(md, encoding="utf-8")
                    _titles = svc.research_engine._get_vault_note_titles(svc.vault_path)
                    _structured = svc.research_engine.synthesize_structured_note(
                        report,
                        summary,
                        ollama_client=svc.ollama_client,
                        vault_note_titles=_titles,
                    )
                    if (
                        _structured
                        and len(_structured)
                        >= svc.research_engine._STRUCTURED_MIN_CHARS
                    ):  # noqa: BLE001 — best-effort, logged and continues
                        Path(note_path).write_text(_structured, encoding="utf-8")
                        session_logger.log(
                            "research_note_structured",
                            {"note_path": note_path, "chars": len(_structured)},
                        )
                report["note_path"] = note_path
            except Exception as e:  # noqa: BLE001 — best-effort, logged and continues
                session_logger.log_exception(e, context="agent_research_note")
        # Citation export: write a .bib file alongside the research note so
        # the sources are available as BibTeX for academic use (DOI extraction
        # + @article/@misc entries). Best-effort — a failure here must never
        # break the research flow. See [[Citation-Export-BibTeX]].
        if report.get("note_path") and report.get("sources"):
            try:
                from citation_exporter import export_citations_to_file

                _bib_path = await loop.run_in_executor(
                    None,
                    export_citations_to_file,
                    report["note_path"],
                    report["sources"],
                )
                session_logger.log(
                    "research_citations_exported",
                    {"note_path": report["note_path"], "bib_path": _bib_path},
                )
            except Exception as e:  # noqa: BLE001 — best-effort
                session_logger.log(
                    "research_citations_export_failed", {"error": str(e)}
                )
        # A-MEM: evolve neighboring notes' tags/links so the vault learns from
        # the new note (arXiv:2502.12110).
        if report.get("note_path"):
            try:
                await send_progress(
                    svc,
                    websocket,
                    "amem_evolve",
                    {"note": Path(report["note_path"]).stem},
                )
                await run_with_heartbeat(
                    svc,
                    websocket,
                    "amem_evolve",
                    lambda: svc.amem.evolve_on_create(
                        report.get("note_path", ""),
                        report.get("synthesis", ""),
                        skip_refresh=True,
                    ),
                )
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log("amem_evolve_failed", {"error": str(e)})
        # Distill the full report to a compact brief before returning so the
        # conversation never balloons (the in-process path doesn't have hard
        # isolation, so this is the bound). Same brief shape as the subagent
        # path so the chat loop + truncate_tool_result work unchanged.
        if isinstance(report, dict):
            try:
                _syn = str(report.get("synthesis", "") or "")
                _facts = report.get("synthesis_facts") or []
                if isinstance(_facts, list):
                    _facts_txt = "\n".join(f"- {str(f)[:300]}" for f in _facts[:8])
                else:
                    _facts_txt = str(_facts)[:1500]
                report = {
                    "topic": report.get("topic"),
                    "source_count": report.get("source_count", 0),
                    "note_path": report.get("note_path"),
                    "synthesis_brief": _syn[:1500]
                    + (
                        "\n*[... full synthesis in the note at note_path ...]*"
                        if len(_syn) > 1500
                        else ""
                    ),
                    "key_facts": _facts_txt,
                    "subagent_note": (
                        "Verbose dig output kept OUT of context (subagent "
                        "isolation). Full synthesis is in the created note; "
                        "re-read it via vault_research/web_read_source if you "
                        "need a specific detail."
                    ),
                }
                session_logger.log(
                    "subagent_result_distilled",
                    {
                        "tool": "vault_research",
                        "orig_synthesis_chars": len(_syn),
                        "brief_chars": len(report.get("synthesis_brief", "")),
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass  # distillation is best-effort; never break the tool
        return report

    if tool_name == "vault_search":
        query = args.get("query", "")
        k = int(args.get("k", 5))
        results = await loop.run_in_executor(None, svc.vault_indexer.search, query, k)
        return {
            "query": query,
            "results": [
                {
                    "file_path": r.get("file_path"),
                    "content": r.get("content", "")[:1200],
                    "score": r.get("score"),
                }
                for r in results
            ],
        }

    if tool_name == "vault_read_note":
        title = (args.get("title") or "").strip()
        max_lines = int(args.get("max_lines", 0))
        if not title:
            return {"error": "missing title"}

        def _read_note_by_title():
            # Try the in-memory graph first (fast, covers the common case).
            node = svc.vault_graph.get_note(title)
            file_path = None
            if node and node.get("file_path"):
                file_path = Path(node["file_path"])
            else:
                # Deferred build may not have indexed it yet — fall back
                # to a deterministic path resolve via rglob stem match.
                resolved = svc.vault_graph._resolve_note_path(title)
                if resolved is not None:
                    file_path = resolved
            if file_path is None or not file_path.exists():
                return {"error": f"note not found: {title}"}
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except Exception as e:  # noqa: BLE001
                return {"error": f"read failed: {e}"}
            s = 1
            e = len(lines) if max_lines <= 0 else min(max_lines, len(lines))
            snippet = "\n".join(lines[s - 1 : e])
            return {
                "file_path": str(file_path),
                "title": file_path.stem,
                "total_lines": len(lines),
                "start_line": s,
                "end_line": e,
                "content": snippet,
            }

        return await loop.run_in_executor(None, _read_note_by_title)

    if tool_name == "vault_gaps":
        gaps = await run_with_heartbeat(
            svc,
            websocket,
            "finding gaps",
            svc.knowledge_curriculum.propose_next_gaps,
            10,
        )
        return {"gaps": gaps[:20], "count": len(gaps)}

    if tool_name == "vault_export_citations":
        from citation_exporter import export_citations, export_citations_to_file

        raw_path = (args.get("file_path") or "").strip()
        if not raw_path:
            return {"error": "missing file_path"}
        fmt = (args.get("format") or "bibtex").strip().lower()

        def _resolve_note_path() -> Path | None:
            p = Path(raw_path)
            if p.is_absolute() and p.exists():
                return p
            # Try relative to vault root.
            vr = Path(svc.vault_path) / raw_path
            if vr.exists():
                return vr
            # Try as a note title → resolve via the vault graph.
            stem = Path(raw_path).stem
            resolved = svc.vault_graph._resolve_note_path(stem)
            if resolved is not None and resolved.exists():
                return resolved
            return None

        def _do_export():
            np = _resolve_note_path()
            if np is None:
                return {"error": f"note not found: {raw_path}"}
            text = export_citations(str(np), format=fmt)
            bib_path = None
            if fmt == "bibtex" and text:
                bib_path = export_citations_to_file(str(np))
            return {
                "format": fmt,
                "note_path": str(np),
                "bib_path": bib_path,
                "citations": text,
            }

        return await loop.run_in_executor(None, _do_export)

    if tool_name == "vaultbot_status":
        return {
            "running": True,
            "research": "on-demand only",
            "tools": [
                "vault_search",
                "vault_research",
                "vault_gaps",
                "execute_procedure",
            ],
            "gaps_count": len(svc.knowledge_curriculum.propose_next_gaps() or []),
        }

    if tool_name == "read_session_log":
        # Read VaultBot's own session logs (issue #134). Wraps
        # session_log_reader.py so the agent can answer "what were we
        # doing last session" from the actual JSONL transcripts, sorted
        # newest-first — NOT from semantic vault_search (which surfaces
        # stale chat notes regardless of recency).
        from session_log_reader import (
            find_session_file,
            format_transcript,
            parse_session_log,
        )

        action = (args.get("action") or "list").strip().lower()
        sessions_dir = Path(__file__).resolve().parent / "sessions"

        def _list_sessions():
            if not sessions_dir.exists():
                return {"error": f"sessions directory not found: {sessions_dir}"}
            count = int(args.get("count", 10))
            files = sorted(
                sessions_dir.glob("*.jsonl"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            out = []
            for f in files[:count]:
                title = "New Session"
                started_at = ""
                try:
                    for line in f.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines():
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if evt.get("event") == "session_title":
                            title = evt.get("title", title)
                            break
                        if evt.get("event") == "session_start":
                            title = evt.get("title", title)
                            started_at = evt.get("started_at", "")
                except OSError:
                    pass
                out.append(
                    {
                        "session_id": f.stem,
                        "title": title,
                        "started_at": started_at,
                        "mtime": f.stat().st_mtime,
                    }
                )
            return {"sessions": out, "count": len(out)}

        def _read_session():
            query = (args.get("session") or "latest").strip()
            target = find_session_file(sessions_dir, query)
            if target is None:
                return {"error": f"no session found for: {query!r}"}
            summary = parse_session_log(target)
            return {
                "session_id": summary["session_id"],
                "title": summary["title"],
                "started_at": summary["started_at"],
                "turns": summary["turns"],
                "tool_calls": [
                    {
                        "tool": tc.get("tool"),
                        "args": tc.get("args"),
                        "result": (tc.get("result") or "")[:500],
                        "error": tc.get("error"),
                    }
                    for tc in summary["tool_calls"]
                ],
                "exceptions": summary["exceptions"],
                "transcript": format_transcript(summary, filter_type="conversation"),
            }

        if action == "list":
            return await loop.run_in_executor(None, _list_sessions)
        if action == "read":
            return await loop.run_in_executor(None, _read_session)
        return {"error": "action must be 'list' or 'read'"}

    # --- Meta-tools (self-improvement) --- #
    if tool_name == "code_read":
        return await loop.run_in_executor(
            None,
            lambda: svc.self_improver.code_read(
                args.get("file_path", ""),
                int(args.get("start_line", 1)),
                int(args.get("end_line", 0)),
            ),
        )

    if tool_name == "code_run":
        return await loop.run_in_executor(
            None,
            lambda: svc.self_improver.code_run(
                args.get("code", ""),
                int(args.get("timeout", 15)),
                bool(args.get("allow_write", False)),
            ),
        )

    if tool_name == "tool_create":
        result = await loop.run_in_executor(
            None,
            lambda: svc.self_improver.tool_create(
                args.get("tool_name", ""),
                args.get("description", ""),
                args.get("parameters", {}),
                args.get("code", ""),
                doc_source=args.get("doc_source"),
            ),
        )
        # Hot-reload so the new tool is callable immediately.
        svc.self_improver.load_custom_tools()
        return result

    if tool_name == "self_reflect":
        ctx = args.get("vault_context", "")
        return await loop.run_in_executor(
            None, lambda: svc.self_improver.self_reflect(args.get("topic", ""), ctx)
        )

    if tool_name == "git_rollback":
        return await loop.run_in_executor(
            None, lambda: svc.self_improver.git_rollback(args.get("file_path", ""))
        )

    if tool_name == "safe_write":
        # Guard: reject empty/missing content before calling safe_write.
        # If the model passes old_str/new_str (md_safe_replace params)
        # instead of content, args.get('content', '') returns '' —
        # safe_write would ast.parse('') successfully and write 0 bytes,
        # destroying the file. Catch this here for a clear error message.
        _sw_content = args.get("content", "")
        if not _sw_content:
            return {
                "error": (
                    "safe_write requires a 'content' parameter with the "
                    "FULL file content. You passed empty or missing content. "
                    "If you meant to do a targeted string replacement, use "
                    "md_safe_replace (.md) or safe_replace (.py) with "
                    "old_str and new_str instead."
                ),
            }
        return await loop.run_in_executor(
            None,
            lambda: svc.self_improver.safe_write(
                args.get("file_path", ""),
                _sw_content,
                bool(args.get("dry_run", False)),
                args.get("doc_source"),
            ),
        )

    if tool_name == "js_safe_write":
        _jsw_content = args.get("content", "")
        if not _jsw_content:
            return {
                "error": (
                    "js_safe_write requires a 'content' parameter with the "
                    "FULL file content. You passed empty or missing content."
                ),
            }
        return await loop.run_in_executor(
            None,
            lambda: svc.self_improver.js_safe_write(
                args.get("file_path", ""),
                _jsw_content,
                bool(args.get("dry_run", False)),
            ),
        )

    if tool_name == "capability_audit":
        return await loop.run_in_executor(
            None, lambda: svc.self_improver.capability_audit(args.get("task", ""))
        )

    # --- Procedure execution (step-gate runtime) --- #
    if tool_name == "execute_procedure":
        proc_name = args.get("procedure_name", "")
        if not proc_name:
            return {"error": "missing procedure_name"}

        async def _proc_progress_cb(
            step_number,
            total_steps,
            output,
            instruction="",
            step_type="text",
            status="running",
            input_preview="",
            elapsed_s=None,
            error="",
        ):
            if websocket is None:
                return
            _is_done = bool(output)
            _payload: dict[str, Any] = {
                "type": "procedure_step",
                "procedure": proc_name,
                "step": step_number,
                "total_steps": total_steps,
                "step_type": step_type,
                "instruction": instruction,
                "phase": "done" if _is_done else "running",
                "status": status,
                "timestamp": time.time(),
            }
            if _is_done:
                _payload["output_preview"] = output[:500]
            if input_preview:
                _payload["input_preview"] = input_preview[:500]
            if elapsed_s is not None:
                _payload["elapsed_s"] = elapsed_s
            if error:
                _payload["error"] = error[:500]
            with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort, must not crash the procedure
                await svc.manager.send_personal_message(
                    json.dumps(_payload), websocket, session_logger=svc.session_logger
                )

        _proc_args = (
            args.get("args")
            if isinstance(args.get("args"), dict)
            else {k: v for k, v in args.items() if k != "procedure_name"}
        )
        core = await dispatch_procedure_core(
            svc,
            proc_name,
            proc_args=_proc_args,
            session_logger=session_logger,
            progress_callback=_proc_progress_cb,
        )
        if "error" in core:
            if core.get("blocked"):
                session_logger.log(
                    "procedure_blocked",
                    {"procedure": proc_name, "status": core.get("status", "unknown")},
                )
            return core

        result = core["result"]
        proc_file = core["proc_file"]
        _cartridge = core["cartridge"]
        _proc_caution = core["proc_caution"]

        session_logger.log(
            "procedure_cartridge",
            {
                "procedure": proc_name,
                "cartridge": _cartridge,
                "model": getattr(svc.ollama_client, "llm_model", "?"),
            },
        )

        if user_message:
            try:
                q_emb = await loop.run_in_executor(
                    None, svc.vault_indexer._get_embedding, user_message
                )
                helpful = result.overall_passed
                svc.embedding_drift.record_feedback(
                    str(proc_file), q_emb, helpful=helpful
                )
                session_logger.log(
                    "procedure_drift_feedback",
                    {
                        "procedure": proc_name,
                        "helpful": helpful,
                        "failed_step": result.failed_step,
                    },
                )
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log("procedure_drift_feedback_failed", {"error": str(e)})
                await notify_console_failure(
                    svc,
                    websocket,
                    f"procedure drift feedback failed: {e}",
                    context="procedure_drift",
                )

        session_logger.log(
            "procedure_result_full",
            {
                "procedure": proc_name,
                "overall_passed": result.overall_passed,
                "failed_step": result.failed_step,
                "steps_executed": len(result.steps),
                "final_output_len": len(result.final_output),
                "final_output_preview": result.final_output[:500],
                "step_details": [
                    {
                        "step": sr.step_number,
                        "type": sr.step_type,
                        "passed": sr.passed,
                        "error": sr.error or sr.validation_error,
                    }
                    for sr in result.steps
                ],
            },
        )

        _return_dict = {
            "procedure": proc_name,
            "overall_passed": result.overall_passed,
            "failed_step": result.failed_step,
            "steps_executed": len(result.steps),
            "final_output": result.final_output[:4000],
            "child_procedures": result.child_procedures,
            "caution": (
                "experimental — unproven procedure; verify the "
                "output before relying on it"
                if _proc_caution
                else ""
            ),
            "step_details": [
                {
                    "step": sr.step_number,
                    "type": sr.step_type,
                    "passed": sr.passed,
                    "error": sr.error or sr.validation_error,
                }
                for sr in result.steps
            ],
        }
        if len(result.steps) == 0:
            _return_dict["diagnosis"] = (
                "PROCEDURE COMPILED 0 STEPS. The procedure compiler "
                "(procedure_compiler.py) parses steps inside a ## Steps "
                "section. The PREFERRED format is:\n"
                "  ### Step N: short summary\n"
                "  ```python\n"
                "  code here\n"
                "  ```\n"
                "\n"
                "  The legacy 'N. ```python ... ```' format also works. "
                "Both require either a '### Step N:' header or a numbered "
                "'N.' line followed by a ```python fence. Check your "
                "procedure's ## Steps section format."
            )
        return _return_dict

    # --- Textbook page reader (index-only paradigm) --- #
    if tool_name == "textbook_read_page":
        from custom_tools.textbook_read_page import run as _read_page

        page_client = (
            svc.vision_client if svc.vision_client is not None else svc.ollama_client
        )
        result = await loop.run_in_executor(
            None, lambda: _read_page(args, llm_client=page_client)
        )
        return result

    # --- Web source re-reader (index-only paradigm for web research) --- #
    if tool_name == "web_read_source":
        from custom_tools.web_read_source import run as _read_web

        result = await loop.run_in_executor(None, lambda: _read_web(args))
        return result

    # --- Working memory (the Copilot/Claude Code TodoList pattern) ------ #
    if tool_name == "plan_task":
        session_logger.log("plan_task_branch_enter", {"t_ms": loop.time() * 1000})
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            wm = TaskList()
            websocket.working_memory = wm
        goal = (args.get("goal") or "").strip()
        steps = args.get("steps") or []
        if not goal or not steps:
            return {"error": "plan_task requires 'goal' and 'steps'"}
        snap = wm.set_plan(goal=goal, items=[s for s in steps if s.strip()])
        websocket._plan_set_round = getattr(websocket, "_chat_round_idx", 0)
        websocket._last_plan_progress_round = websocket._plan_set_round
        session_logger.log(
            "plan_task_set",
            {
                "goal": goal[:100],
                "steps": len(steps),
                "round": websocket._plan_set_round,
            },
        )
        try:
            _full_snap = wm.snapshot()
            session_logger.log("plan_snapshot", _full_snap)
        except Exception:  # noqa: BLE001 — best-effort
            pass
        session_logger.log("plan_task_branch_exit", {"t_ms": loop.time() * 1000})
        return snap

    if tool_name == "update_task":
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            return {"error": "no active plan"}
        task_id = args.get("task_id") or ""
        _new_status = args.get("status", "")
        _was_completed = False
        try:
            for _t in wm.tasks:
                if _t.id == task_id:
                    _was_completed = _t.status == "completed"
                    break
        except Exception:  # noqa: BLE001 — best-effort
            pass
        snap = wm.update_task(
            task_id=task_id, status=_new_status, notes=args.get("notes", "")
        )
        if _new_status == "completed" and not _was_completed:
            websocket._last_plan_progress_round = getattr(
                websocket, "_chat_round_idx", 0
            )
        session_logger.log(
            "plan_task_updated",
            {
                "task_id": task_id,
                "status": _new_status,
                "was_completed": _was_completed,
                "counted_as_progress": (
                    _new_status == "completed" and not _was_completed
                ),
            },
        )
        try:
            _full_snap = wm.snapshot()
            session_logger.log("plan_snapshot", _full_snap)
        except Exception:  # noqa: BLE001 — best-effort
            pass
        return snap

    if tool_name == "add_task":
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            return {"error": "no active plan"}
        content = (args.get("content") or "").strip()
        if not content:
            return {"error": "add_task requires 'content'"}
        snap = wm.add_task(
            content=content,
            status=args.get("status", "pending"),
            notes=args.get("notes", ""),
        )
        websocket._last_plan_progress_round = getattr(websocket, "_chat_round_idx", 0)
        session_logger.log("plan_task_added", {"content": content[:80]})
        return snap

    # --- Custom (agent-authored) tools --- #
    if svc.self_improver.has_tool(tool_name):
        if tool_name == "backend_restart":
            try:
                _wm = getattr(websocket, "working_memory", None)
                if _wm is not None and _wm.has_plan():
                    _wm.save_to_disk(session_id=getattr(websocket, "session_id", None))
                    session_logger.log(
                        "wm_force_saved_before_restart",
                        {
                            "goal": _wm.goal[:100],
                            "tasks": len(_wm.tasks),
                        },
                    )
                # Force-save the LIVE conversation before restarting. The
                # live ``conversation`` list (passed down from the agentic
                # loop) is the ACTUAL current thread — it may be far longer
                # than ``websocket.conversation_history``, which is only
                # synced to the live list at the END of a turn. A mid-turn
                # restart that reads only the stale websocket copy would
                # persist a truncated thread and lose the whole live turn.
                # Prefer the live list; fall back to the websocket copy.
                _conv = conversation or getattr(websocket, "conversation_history", None)
                if _conv:
                    from conversation_state import save_history

                    save_history(
                        _conv, session_id=getattr(websocket, "session_id", None)
                    )
                    session_logger.log(
                        "conv_force_saved_before_restart",
                        {
                            "turns": len(_conv),
                            "source": (
                                "live_conversation"
                                if conversation
                                else "websocket_history"
                            ),
                        },
                    )
            except Exception as _e:  # noqa: BLE001 — best-effort
                session_logger.log(
                    "force_save_before_restart_failed", {"error": str(_e)}
                )
        if tool_name == "ask_user":

            def _run_ask_user():
                from custom_tools.ask_user import run as _ask_run

                return _ask_run(
                    args,
                    websocket=websocket,
                    session_id=getattr(websocket, "session_id", None),
                )

            result = await loop.run_in_executor(None, _run_ask_user)
        else:
            result = await loop.run_in_executor(
                None, lambda: svc.self_improver.execute_custom_tool(tool_name, args)
            )
        if tool_name == "textbook_ingest" and isinstance(result, dict):
            note_count = len(
                result.get("notes_created", []) + result.get("notes_updated", [])
            )
            if note_count > 0:
                result["weaving"] = {
                    "status": "background",
                    "notes_to_weave": note_count,
                    "message": (
                        f"Weaving {note_count} notes into the vault "
                        f"in the background (indexing + linking + "
                        f"evolving neighbors)..."
                    ),
                }

                async def _run_weave_bg():
                    try:
                        await weave_textbook_notes(
                            svc,
                            result,
                            websocket=websocket,
                            session_logger=session_logger,
                        )
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log(
                            "textbook_weave_bg_failed", {"error": str(e)}
                        )
                        await notify_problem(
                            svc,
                            websocket,
                            e,
                            context={"stage": "weaving textbook notes"},
                            user_message=(
                                "Something went wrong while linking your "
                                "textbook notes into the vault. The notes "
                                "are saved, but they won't be connected to "
                                "other notes until this is fixed."
                            ),
                            remedy_hint=(
                                "Try restarting VaultBot. If it keeps "
                                "happening, use Copy for support."
                            ),
                        )

                _task = asyncio.create_task(_run_weave_bg())
                _background_tasks.add(_task)
                _task.add_done_callback(_background_tasks.discard)
        return result

    # Unknown tool (issue #130): return a SHORT error, never echo a long
    # garbled tool name back into context. A corrupted name (prior tool
    # result text) would otherwise be injected verbatim into the next
    # round, poisoning the conversation. Truncate defensively.
    _short_name = (tool_name or "")[:40]
    return {"error": f"unknown tool: {_short_name}"}
