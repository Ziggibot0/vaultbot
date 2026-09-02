import asyncio
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chat_helpers import run_with_heartbeat, send_progress
from services import Services
from subagent import run_research_subagent, subagent_enabled


async def execute_vault_research(
    svc: Services,
    args: dict[str, Any],
    session_logger,
    websocket=None,
) -> dict[str, Any]:
    allow_research = os.environ.get("VAULTBOT_ALLOW_WEB_RESEARCH", "true")
    if allow_research.strip().lower() in {"0", "false", "off", "no"}:
        return {
            "error": "Web research is disabled. Set 'Allow web research' in "
            "VaultBot Settings to enable it."
        }

    if not (topic := (args.get("topic") or "").strip()):
        return {"error": "missing topic"}

    ctx = SimpleNamespace(
        svc=svc,
        websocket=websocket,
        logger=session_logger,
        topic=topic,
        args=args,
        loop=asyncio.get_event_loop(),
        heartbeat=partial(run_with_heartbeat, svc, websocket),
        progress=partial(send_progress, svc, websocket),
        log=lambda event, **detail: session_logger.log(event, detail),
    )

    try:
        if subagent_enabled():
            return await _run_subagent(ctx)
    except Exception as exc:  # noqa: BLE001
        session_logger.log("subagent_enablement_failed", {"error": str(exc)})
    return await _run_in_process(ctx)


async def _run_subagent(ctx) -> dict[str, Any]:
    logger, args = ctx.logger, ctx.args
    ctx.log("subagent_research_invoked", topic=ctx.topic[:80])
    started = ctx.loop.time()
    try:
        brief = await ctx.heartbeat(
            f"research{ctx.topic[:40]}",
            run_research_subagent,
            ctx.topic,
            args.get("depth", "deep"),
            logger,
            args.get("source_allowlist"),
            args.get("source_denylist"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.log_exception(exc, context="subagent_research")
        brief = dict(
            status="error", error=f"subagent research failed: {exc}", subagent=True
        )

    try:
        await ctx.loop.run_in_executor(None, ctx.svc.vault_graph.refresh)
    except Exception as exc:  # noqa: BLE001
        ctx.log("post_subagent_graph_refresh_failed", error=str(exc))

    ctx.log(
        "subagent_research_complete",
        duration_ms=int((ctx.loop.time() - started) * 1000),
        status=brief.get("status"),
        source_count=brief.get("source_count", 0),
        note_path=brief.get("note_path"),
    )
    if brief.get("status") == "empty":
        brief.setdefault("error", "no web sources found")
    return brief


async def _run_in_process(ctx) -> dict[str, Any]:
    engine = ctx.svc.research_engine
    previous_state = engine.max_rounds, engine.max_follow_ups, engine.progress_callback
    if ctx.args.get("depth", "deep") == "quick":
        engine.max_rounds = 1
        engine.max_follow_ups = 0

    if ctx.websocket is not None:

        def progress_callback(stage: str, detail: dict):
            try:
                asyncio.run_coroutine_threadsafe(ctx.progress(stage, detail), ctx.loop)
            except Exception as exc:  # noqa: BLE001
                ctx.log("tool_progress_cb_failed", error=str(exc))

        engine.progress_callback = progress_callback

    started = ctx.loop.time()
    report: dict[str, Any] | None = None
    try:
        report = await ctx.heartbeat(
            f"research{ctx.topic[:40]}",
            engine.research,
            ctx.topic,
            None,
            None,
            ctx.args.get("source_allowlist"),
            ctx.args.get("source_denylist"),
        )
    finally:
        engine.max_rounds, engine.max_follow_ups, engine.progress_callback = (
            previous_state
        )
        ctx.log(
            "agent_research_done",
            duration_ms=(ctx.loop.time() - started) * 1000,
            source_count=report.get("source_count", 0)
            if isinstance(report, dict)
            else 0,
        )

    if not isinstance(report, dict):
        return {"error": "research returned an invalid result"}

    await _persist_note(ctx, report)
    await _export_citations(ctx, report)
    await _evolve_memory(ctx, report)
    return _distill_report(ctx.logger, report)


async def _persist_note(ctx, report) -> None:
    if not (report.get("source_count") and report.get("synthesis")):
        return
    svc, logger = ctx.svc, ctx.logger
    try:
        summary = (
            f"Research into '{ctx.topic}' ({report['source_count']} sources, "
            f"{report['synthesis_facts']} facts)."
        )
        await ctx.progress("writing_note", {"topic": ctx.topic})
        path = await ctx.heartbeat(
            "writing_note",
            svc.note_creator.create_note_from_research,
            ctx.topic,
            report["synthesis"],
            summary,
        )
        if report.get("llm_synthesized"):
            try:
                from note_schema import inject_schema

                sanitized = inject_schema(
                    report["synthesis"],
                    f"vaultbot-stuff/Knowledge/Research/{ctx.topic}.md",
                    force_type="research",
                )
                Path(path).write_text(sanitized, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                ctx.log(
                    "research_note_write_failed",
                    path=path,
                    error=str(exc),
                )
        else:
            engine = svc.research_engine
            markdown = engine.synthesize_note_markdown(report, summary)
            Path(path).write_text(markdown, encoding="utf-8")
            titles = engine._get_vault_note_titles(svc.vault_path)
            structured = engine.synthesize_structured_note(
                report,
                summary,
                ollama_client=svc.ollama_client,
                vault_note_titles=titles,
            )
            if structured and len(structured) >= engine._STRUCTURED_MIN_CHARS:
                Path(path).write_text(structured, encoding="utf-8")
                ctx.log(
                    "research_note_structured",
                    note_path=path,
                    chars=len(structured),
                )
        report["note_path"] = path
    except Exception as exc:  # noqa: BLE001
        logger.log_exception(exc, context="agent_research_note")


async def _export_citations(ctx, report) -> None:
    if not (report.get("note_path") and report.get("sources")):
        return
    try:
        from citation_exporter import export_citations_to_file

        bib_path = await ctx.loop.run_in_executor(
            None,
            export_citations_to_file,
            report["note_path"],
            report["sources"],
        )
        ctx.log(
            "research_citations_exported",
            note_path=report["note_path"],
            bib_path=bib_path,
        )
    except Exception as exc:  # noqa: BLE001
        ctx.log("research_citations_export_failed", error=str(exc))


async def _evolve_memory(ctx, report) -> None:
    if not report.get("note_path"):
        return
    try:
        await ctx.progress("amem_evolve", {"note": Path(report["note_path"]).stem})
        await ctx.heartbeat(
            "amem_evolve",
            lambda: ctx.svc.amem.evolve_on_create(
                report.get("note_path", ""),
                report.get("synthesis", ""),
                skip_refresh=True,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        ctx.log("amem_evolve_failed", error=str(exc))


def _distill_report(session_logger, report: dict[str, Any]) -> dict[str, Any]:
    synthesis = str(report.get("synthesis", "") or "")
    facts = report.get("synthesis_facts") or []
    facts_text = (
        "\n".join(f"- {str(fact)[:300]}" for fact in facts[:8])
        if isinstance(facts, list)
        else str(facts)[:1500]
    )
    suffix = (
        "\n*[... full synthesis in the note at note_path ...]*"
        if len(synthesis) > 1500
        else ""
    )
    brief = {
        "topic": report.get("topic"),
        "source_count": report.get("source_count", 0),
        "note_path": report.get("note_path"),
        "synthesis_brief": synthesis[:1500] + suffix,
        "key_facts": facts_text,
        "subagent_note": (
            "Verbose dig output kept OUT of context (subagent isolation). "
            "Full synthesis is in the created note; re-read it via "
            "vault_research/web_read_source if you need a specific detail."
        ),
    }
    session_logger.log(
        "subagent_result_distilled",
        {
            "tool": "vault_research",
            "orig_synthesis_chars": len(synthesis),
            "brief_chars": len(brief["synthesis_brief"]),
        },
    )
    return brief
