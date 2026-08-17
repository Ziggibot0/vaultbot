"""Synthesis functions extracted from research_engine.py.

These were originally methods of ``ResearchEngine``; they've been extracted
to keep ``research_engine.py`` under the line-count target.  The
``ResearchEngine`` class still exposes them as thin delegating methods so
external callers (chat_tool_dispatch, research_handler, routers/research,
subagent) don't break.

Functions:
  - ``llm_synthesize`` — one LLM call to synthesize source texts.
  - ``extractive_synthesis`` — deterministic sentence-scoring fallback.
  - ``repair_wikilinks`` — fix case/hallucinated [[wikilinks]].
  - ``get_vault_note_titles`` — scan vault dir for real .md filenames.
  - ``synthesize_note_markdown`` — render a report as Obsidian markdown.
  - ``synthesize_structured_note`` — LLM-restructured research note.
"""

from __future__ import annotations

import re
from typing import Any

from citation_exporter import extract_doi as _extract_doi
from text_scoring import (
    score_sentence as _score_sentence,
    split_sentences as _split_sentences,
    tokenize_light as _tokenize_light,
)

# Safety floor for LLM-structured notes (reject output shorter than this).
_STRUCTURED_MIN_CHARS = 500


# ── helpers needed by extractive_synthesis ─────────────────────────────


def _corroborated_facts(
    corroborate_fn,
    sentences: list[tuple[str, dict[str, Any]]],
    keyterms: list[str],
) -> list[dict[str, Any]]:
    """Delegate to the engine's _corroborated_facts (kept in research_engine)."""
    return corroborate_fn(sentences, keyterms)


# ── public functions ───────────────────────────────────────────────────


def llm_synthesize(
    topic: str,
    sources: list[dict[str, Any]],
    llm_client: Any,
    vault_note_titles: list[str] | None = None,
    log_fn=None,
) -> str | None:
    """One LLM call to synthesize a structured research note from source texts.

    Produces YAML frontmatter + H2 prose sections with inline
    [sources: ...] citations and [[wikilinks]] to existing vault notes.
    The LLM naturally filters irrelevant sources because it understands
    the topic. Returns the synthesis string or None if the LLM fails /
    produces too-short output.
    """
    # Build source texts block, capped to avoid overflowing context.
    source_blocks = []
    total_chars = 0
    max_chars = 12000  # Leave room for the prompt itself
    for i, src in enumerate(sources):
        title = src.get("title") or src.get("url", f"Source {i + 1}")
        text = src.get("text", "")[:3000]  # Cap each source
        block = f"### Source {i + 1}: {title}\n{text}"
        if total_chars + len(block) > max_chars:
            break
        source_blocks.append(block)
        total_chars += len(block)

    sources_text = "\n\n".join(source_blocks)

    # Build vault titles hint so the LLM can insert real wikilinks.
    titles_hint = ""
    if vault_note_titles:
        sample = vault_note_titles[:150]
        titles_hint = (
            "\n\nEXISTING VAULT NOTES (link to any that are topically "
            "relevant using [[Note-Name]] -- do NOT force links, do NOT "
            "invent titles not in this list):\n" + "\n".join(f"- {t}" for t in sample)
        )

    system = (
        "You are a research synthesis assistant for an Obsidian knowledge "
        "vault. You take multiple source texts on a topic and produce a "
        "structured research note. You MUST follow ALL of these rules:\n"
        "1. START with YAML frontmatter (--- ... ---) containing: type: "
        "research, status: raw, created: today's date, summary: one-line "
        "description, tags: [research, <topic-keywords>], source_count, "
        "fact_count. The frontmatter is MANDATORY.\n"
        "2. Write 2-4 ## H2 section headings that organize the content "
        "into a narrative. Each section MUST be PROSE PARAGRAPHS that "
        "build an argument -- NOT bullet points, NOT flat lists. Write "
        "connected sentences that flow. This is non-negotiable.\n"
        "3. After each claim, cite the source title in [sources: ...] tags.\n"
        "4. SKIP any source that is not relevant to the topic.\n"
        "5. Insert [[wikilinks]] to existing vault notes ONLY where "
        "topically relevant (use the EXISTING VAULT NOTES list). Never "
        "invent note titles that aren't in that list. Link to at least "
        "2 relevant existing notes if any are topically related.\n"
        "6. Keep ALL the factual content -- don't drop facts, just "
        "weave them into readable prose.\n"
        "7. End with a ## Sources section listing each source as a "
        "markdown link: - [Title](URL)\n"
        "8. Do NOT add a top-level # heading. Start with the YAML "
        "frontmatter.\n"
        "9. Output ONLY the note content, nothing else."
    )

    # Build source list for the Sources section, including DOI when
    # extractable from the URL (academic citations — a PhD reviewer
    # needs DOIs, not just markdown links). See [[Citation-Export-BibTeX]].
    source_list_lines: list[str] = []
    for i, s in enumerate(sources):
        _title = s.get("title", s.get("url", f"Source {i + 1}"))
        _url = s.get("url", "")
        _doi = _extract_doi(_url)
        if _doi:
            source_list_lines.append(f"- [{_title}]({_url}) — DOI: {_doi}")
        else:
            source_list_lines.append(f"- [{_title}]({_url})")
    source_list = "\n".join(source_list_lines)

    user = (
        f"Topic: {topic}\n\n"
        f"Source texts:\n\n{sources_text}\n\n"
        f"{titles_hint}\n\n"
        f"Source list for the Sources section:\n{source_list}\n\n"
        f"Write a structured research note about '{topic}' from these "
        f"sources. Start with YAML frontmatter, then 2-4 H2 prose "
        f"sections with [sources: ...] citations and [[wikilinks]] to "
        f"relevant existing vault notes. End with a ## Sources section "
        f"using the source list above. Skip irrelevant sources."
    )

    try:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        result = llm_client.chat(messages, temperature=0.3, stream=False)
        if isinstance(result, dict):
            synthesis = result.get("response", "")
        else:
            synthesis = ""

        synthesis = (synthesis or "").strip()
        if len(synthesis) < 200:
            return None

        # Deterministic wikilink repair: fix case mismatches and remove
        # hallucinated titles. Pure string matching, zero LLM calls.
        if vault_note_titles:
            synthesis = repair_wikilinks(synthesis, vault_note_titles)

        return synthesis
    except Exception as e:
        # Log the error and re-raise — no silent extractive fallback.
        # The caller will see the LLM synthesis failed.
        if log_fn is not None:
            log_fn("llm_synthesis_exception", f"{type(e).__name__}: {e}")
        raise


def extractive_synthesis(
    all_sources: list[dict[str, Any]],
    base_terms: list[str],
    corroborate_fn=None,
) -> tuple[str, set]:
    """Deterministic extractive synthesis (fallback when no LLM available).

    Scores sentences by keyword density + source agreement, dedups
    near-identical sentences, and returns the top findings as bullet
    points with [sources: ...] citations. Returns (synthesis, used_set).

    ``corroborate_fn`` is the ``ResearchEngine._corroborated_facts``
    method, passed in so this function stays decoupled from the class.
    """
    sentences: list[tuple[str, dict[str, Any]]] = []
    for src in all_sources:
        for sent in _split_sentences(src["text"]):
            sentences.append((sent, src))
    if corroborate_fn is not None:
        facts = corroborate_fn(sentences, base_terms)
    else:
        # Fallback: simple scoring without source agreement.
        facts = [
            {
                "sentence": s,
                "sources": [src],
                "score": _score_sentence(s, base_terms, 1),
            }
            for s, src in sentences
        ]
    synthesis_lines: list[str] = []
    total_len = 0
    max_synth = 3500
    used: set = set()
    for fact in facts:
        s = fact["sentence"]
        sig = tuple(sorted(_tokenize_light(s)))
        if sig in used:
            continue
        used.add(sig)
        srcs = ", ".join(f["title"] or f["url"] for f in fact["sources"][:2])
        line = f"- {s}  [sources: {srcs}]"
        if total_len + len(line) > max_synth:
            break
        synthesis_lines.append(line)
        total_len += len(line)
    return "\n".join(synthesis_lines), used


def get_vault_note_titles(vault_path: str) -> list[str]:
    """Get actual note titles (preserving case) from the vault directory.

    VaultGraph.nodes.keys() returns lowercase titles, but repair_wikilinks
    needs the actual filename casing to fix case-mismatched wikilinks.
    This scans disk for real .md filenames.
    """
    import glob
    import os as _os

    titles = []
    for f in glob.glob(_os.path.join(vault_path, "**", "*.md"), recursive=True):
        title = _os.path.splitext(_os.path.basename(f))[0]
        titles.append(title)
    return titles


def repair_wikilinks(note_md: str, valid_titles: list[str]) -> str:
    """Fix wikilinks in LLM-generated note text. Zero LLM calls.

    Two problems this fixes:
    1. Case mismatch: LLM writes [[cell-structure-organelles]] but the actual
       note is [[Cell-Structure-Organelles]]. Fixed by case-insensitive match.
    2. Hallucinated titles: LLM writes [[some-note-that-doesnt-exist]].
       Fixed by converting to plain text (strip the brackets).

    Also handles piped wikilinks: [[Note-Name|display text]].
    """
    if not valid_titles:
        return note_md

    # Build a case-insensitive lookup: lowercase_title -> actual_title
    title_lookup = {}
    for t in valid_titles:
        clean = t[:-3] if t.endswith(".md") else t
        title_lookup[clean.lower()] = clean

    def _fix_link(m):
        inner = m.group(1)
        if "|" in inner:
            target, display = inner.split("|", 1)
        else:
            target, display = inner, inner

        target_lower = target.strip().lower()
        if target_lower in title_lookup:
            actual = title_lookup[target_lower]
            if display.strip() == target.strip():
                return f"[[{actual}]]"
            else:
                return f"[[{actual}|{display.strip()}]]"
        else:
            return display.strip()

    # Match [[...]] but not inside code blocks
    parts = note_md.split("```")
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\[\[([^\]]+)\]\]", _fix_link, parts[i])
    return "```".join(parts)


def synthesize_note_markdown(report: dict[str, Any], summary: str | None = None) -> str:
    """Render a research report as Obsidian markdown (no LLM).

    This is the deterministic fallback when LLM synthesis is unavailable.
    Includes YAML frontmatter so even the fallback has metadata.
    """
    from datetime import date

    _topic = report.get("topic", "Research Note")
    _src_count = report.get("source_count", 0)
    _facts = report.get("synthesis_facts", 0)
    # Build the body content — frontmatter is injected by note_schema
    lines = [f"# {_topic}", ""]
    if summary:
        lines += ["## Summary", summary, ""]
    lines += [
        "## Key Findings",
        report["synthesis"] or "(no corroborated findings extracted)",
        "",
        "## Sources",
    ]
    for s in report["sources"]:
        try:
            from web_source_store import find_source

            archived = find_source(s["url"])
        except Exception:  # noqa: BLE001 — best-effort
            archived = None
        if archived:
            local = f"[[learningMaterial/web/{archived['file']}|archived]]"
            lines.append(f"- [{s['title'] or s['url']}]({s['url']}) ({local})")
        else:
            lines.append(f"- [{s['title'] or s['url']}]({s['url']})")
    if report.get("gaps_filled"):
        lines += [
            "",
            "## Follow-up Queries (gap fill)",
            "\n".join(f"- {g}" for g in report["gaps_filled"]),
        ]
    lines += [
        "",
        f"<!-- research: {report['source_count']} sources, "
        f"{report['synthesis_facts']} facts, "
        f"{len(report.get('rounds', []))} rounds -->",
    ]
    body = "\n".join(lines)

    # Inject universal schema frontmatter
    try:
        from note_schema import inject_schema

        return inject_schema(
            body,
            f"vaultbot/Knowledge/Research/{_topic}.md",
            force_type="research",
        )
    except ImportError:
        # Fallback: manual frontmatter if note_schema unavailable
        fm = [
            "---",
            "type: research",
            "status: raw",
            f"created: {date.today().isoformat()}",
            f"summary: {summary or f'Deep research into {_topic}'}",
            "tags: [research]",
            f"source_count: {_src_count}",
            f"fact_count: {_facts}",
            "---",
            "",
        ]
        return "\n".join(fm) + body


def synthesize_structured_note(
    report: dict[str, Any],
    summary: str | None = None,
    ollama_client: Any = None,
    vault_note_titles: list[str] | None = None,
) -> str:
    """Restructure the extractive synthesis into a proper research note.

    ONE LLM call. Produces a note with YAML frontmatter, H2 sections,
    argument-driven narrative, preserved [sources: ...] citations, and
    [[wikilinks]] to existing vault notes.

    Raises if the LLM is unavailable or produces insufficient output —
    no silent fallback to the extractive format. The caller must
    decide whether to use ``synthesize_note_markdown`` explicitly.
    """
    if ollama_client is None:
        raise ValueError(
            "synthesize_structured_note: ollama_client is required — "
            "use synthesize_note_markdown() for the extractive format"
        )
    synth = str(report.get("synthesis", "") or "")
    if len(synth) < 80:
        raise ValueError(
            f"synthesize_structured_note: synthesis too short "
            f"({len(synth)} chars, need >=80)"
        )

    topic = report.get("topic", "Research Note")
    source_count = report.get("source_count", 0)
    facts = report.get("synthesis_facts", 0)

    # Build the source list for the prompt (title + url).
    sources_block = "\n".join(
        f"- {s.get('title') or s.get('url', '')} — {s.get('url', '')}"
        for s in report.get("sources", [])[:12]
    )

    # Build a vault-link hint: a compact list of existing note titles so
    # the LLM can insert [[wikilinks]] to relevant concepts. Capped to
    # avoid flooding the prompt (the LLM only needs a sample to spot
    # relevant ones).
    titles_hint = ""
    if vault_note_titles:
        sample = vault_note_titles[:120]
        titles_hint = (
            "\n\nEXISTING VAULT NOTES (use [[Note-Name]] to link to any "
            "that are topically relevant — do NOT force links):\n"
            + "\n".join(f"- {t}" for t in sample)
        )

    # Build the prompt. The system message sets the format contract;
    # the user message provides the raw synthesis + sources.
    system = (
        "You are a research note structuring assistant. You take raw "
        "extractive synthesis (corroborated sentences with [sources: ...] "
        "tags) and restructure it into a proper Obsidian research note. "
        "You MUST:\n"
        "1. Start with YAML frontmatter (--- ... ---) with keys: type, "
        "status, created, summary, tags. For claim-like notes also use: "
        "supports, contradicts, derived_from, confidence, "
        "falsifiable_if (all optional — only when the note makes a "
        "verifiable claim).\n"
        "2. Use ## H2 section headings to organize the content into a "
        "narrative (NOT flat bullet points). Each section should build "
        "an argument, not just list facts.\n"
        "3. PRESERVE every [sources: ...] citation tag inline — these "
        "are the provenance links.\n"
        "4. Insert [[wikilinks]] to existing vault notes ONLY where "
        "topically relevant (use the EXISTING VAULT NOTES list). Never "
        "invent note titles that aren't in that list.\n"
        "5. Keep ALL the factual content from the synthesis — don't "
        "drop facts, just restructure them into readable prose.\n"
        "6. ONE IDEA PER NOTE: If the research covers multiple distinct "
        "claims, mention them but keep this note focused on the main "
        "claim. The vault can split notes later.\n"
        "7. End with a ## Sources section listing each source as a "
        "markdown link.\n"
        "Do NOT add a top-level # heading (the caller adds it). Start "
        "directly with the YAML frontmatter."
    )
    user = (
        f"Topic: {topic}\n\n"
        f"Summary line: {summary or ''}\n\n"
        f"Raw extractive synthesis ({source_count} sources, {facts} "
        f"facts):\n\n{synth}\n\n"
        f"Sources:\n{sources_block}"
        f"{titles_hint}\n\n"
        "Restructure this into a proper research note with YAML "
        "frontmatter, H2 sections, preserved citations, and "
        "[[wikilinks]] to relevant existing vault notes. Output ONLY "
        "the note content (starting with ---)."
    )

    try:
        result = ollama_client.generate(
            prompt=user,
            system=system,
            temperature=0.3,
            max_tokens=2048,
            stream=False,
        )
        if isinstance(result, dict):
            note_md = result.get("response", "")
        else:
            # A generator fallback — drain it (shouldn't happen with
            # stream=False, but be safe).
            note_md = "".join(c.get("response", "") for c in result)
    except Exception as e:
        raise RuntimeError(f"synthesize_structured_note: LLM call failed: {e}") from e

    note_md = (note_md or "").strip()
    if len(note_md) < _STRUCTURED_MIN_CHARS:
        raise RuntimeError(
            f"synthesize_structured_note: LLM output too short "
            f"({len(note_md)} chars, need >= {_STRUCTURED_MIN_CHARS})"
        )

    # The LLM may have included a top-level # heading despite the
    # instruction not to. Strip it so the caller's own # heading is
    # the only one at the top.
    note_md = re.sub(r"\A#\s+.+\n+", "", note_md)

    # Ensure the research provenance marker is present (the extractive
    # format has it; the LLM may drop it). Append if missing.
    marker = (
        f"<!-- research: {source_count} sources, {facts} facts, "
        f"{len(report.get('rounds', []))} rounds -->"
    )
    if marker not in note_md:
        note_md = note_md.rstrip() + "\n\n" + marker + "\n"

    # Deterministic wikilink repair: the LLM often generates wikilinks
    # with wrong casing or hallucinates titles. Fix without extra LLM
    # calls -- pure string matching against actual vault note titles.
    if vault_note_titles:
        note_md = repair_wikilinks(note_md, vault_note_titles)

    # Inject universal schema to fill any required fields the LLM forgot
    try:
        from note_schema import inject_schema

        note_md = inject_schema(
            note_md,
            f"vaultbot/Knowledge/Research/{topic}.md",
            force_type="research",
        )
    except ImportError:
        pass

    return note_md
