"""Research + learning-material ingestion endpoints.

Migrated from main.py. Handlers read singletons via Depends(get_services).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

from app_state import get_services
from fastapi import APIRouter, Depends
from services import Services

router = APIRouter()


@router.post("/research_tool")
async def research_tool_endpoint(
    payload: dict, svc: Annotated[Services, Depends(get_services)]
):
    """Deep-research a topic via the LLM-light engine. Used by the MCP server
    and by any client that wants a sourced dig without invoking the LLM.

    Returns the structured research report plus the path of the note written.
    """
    topic = (payload.get("topic") or "").strip()
    depth = payload.get("depth", "deep")
    if not topic:
        return {"error": "missing topic"}, 400
    loop = asyncio.get_event_loop()
    if depth == "quick":
        # Quick mode: one round, no gap fill.
        svc.research_engine.max_rounds = 1
        svc.research_engine.max_follow_ups = 0
    try:
        _titles = svc.research_engine._get_vault_note_titles(svc.vault_path)
        report = await loop.run_in_executor(
            None,
            lambda: svc.research_engine.research(
                topic, llm_client=svc.ollama_client, vault_note_titles=_titles
            ),
        )
    finally:
        # Restore defaults so the autonomous researcher isn't affected.
        svc.research_engine.max_rounds = int(os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4"))
        svc.research_engine.max_follow_ups = int(
            os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3")
        )

    # Persist a linked research note so the dig becomes vault knowledge.
    note_path = None
    if report.get("source_count") and report.get("synthesis"):
        try:
            summary = (
                f"Deep research into '{topic}' ({report['source_count']} "
                f"sources, {report['synthesis_facts']} facts)."
            )
            note_path = await loop.run_in_executor(
                None,
                svc.note_creator.create_note_from_research,
                topic,
                report["synthesis"],
                summary,
            )
            if report.get("llm_synthesized"):
                # LLM synthesis already produced a structured note with
                # frontmatter, H2 prose sections, wikilinks, and Sources.
                # Write it directly -- skip double-processing.
                Path(note_path).write_text(report["synthesis"], encoding="utf-8")
            else:
                # Extractive synthesis: wrap in markdown, then try LLM
                # structuring (ONE call) for frontmatter + H2 sections.
                md = svc.research_engine.synthesize_note_markdown(report, summary)
                Path(note_path).write_text(md, encoding="utf-8")
                # LLM structuring is a separate explicit step — if it fails,
                # raise so the user sees the error. The extractive markdown
                # is already saved; the user can re-run structuring later.
                _structured = svc.research_engine.synthesize_structured_note(
                    report,
                    summary,
                    ollama_client=svc.ollama_client,
                    vault_note_titles=_titles,
                )
                if (
                    _structured
                    and len(_structured) >= svc.research_engine._STRUCTURED_MIN_CHARS
                ):
                    Path(note_path).write_text(_structured, encoding="utf-8")
        except Exception as e:
            svc.session_logger.log_exception(e, context="research_tool_note")
            raise
    report["note_path"] = note_path

    # Run claim verification on the newly written note.
    if note_path:
        try:
            verification = await loop.run_in_executor(
                None, svc.claim_verifier.verify_note, note_path
            )
            report["verification"] = verification
            if (
                verification.get("unsupported", 0) + verification.get("contradicted", 0)
                > 0
            ):
                svc.session_logger.log(
                    "claim_verification",
                    f"Note {note_path}: {verification['verified']}/{verification['total_claims']} verified, "
                    f"{verification['unsupported']} unsupported, {verification['contradicted']} contradicted",
                )
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            svc.session_logger.log_exception(e, context="claim_verification")

    return report


@router.post("/ingest_learning_material")
async def ingest_learning_material_endpoint(
    payload: dict | None = None, svc: Annotated[Services, Depends(get_services)] = None
):
    """Index any new PDFs from learningMaterial/ as pointer-only TOCs.

    Returns a summary of what was indexed. Idempotent: a PDF already indexed
    (its source-key is in an existing index TOC) is skipped.
    """
    from textbook_index import index_learning_material

    payload = payload or {}
    loop = asyncio.get_event_loop()
    vault_root = Path(os.getenv("VAULT_PATH", "."))
    learning_dir = vault_root / "vaultbot/learningMaterial"
    result = await loop.run_in_executor(
        None, lambda: index_learning_material(str(learning_dir))
    )
    return result
