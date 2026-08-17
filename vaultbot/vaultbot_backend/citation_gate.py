"""Closed-set citation enforcement — the vault-centric provenance gate.

The big LLM is a ROUTER/SYNTHESIZER over vault notes, not a knowledge
source. This module builds the set of notes the model is ALLOWED to cite
(the "closed set"), parses [[wikilinks]] out of an answer, scores how
grounded the answer is against that set, and produces the reprimand the
grounding gate sends back when the answer is ungrounded.

The closed set is per-turn and dynamic:
  - Seeded from the retrieved vault context at preflight (the `### [[Name]]`
    headers in the rendered context block).
  - Extended whenever the model calls vault_search / vault_read_note
    mid-loop, so notes the model retrieved on its own are also valid
    citation targets.
  - Rebuilt fresh every turn (a note cited in turn 1 isn't automatically
    valid in turn 5 unless re-retrieved).

This is a pure leaf module — no I/O, no Services dependency, no asyncio.
It operates on strings and dicts so it can be unit-tested in isolation
and imported from chat_turn_prep / chat_turn_finalize / chat_tool_dispatch
without circular-import risk.
"""

from __future__ import annotations

import re
from typing import Any

from config import TUNABLES

# ── [[wikilink]] parsing ──────────────────────────────────────────────────
# Matches [[Note-Name]], [[Note-Name|alias]], [[Note-Name#heading]].
# Capture group 1 = the note stem (what we compare against the closed set).
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def extract_wikilinks(text: str) -> list[str]:
    """Return all [[wikilink]] stems from `text`, deduped, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK_RE.finditer(text or ""):
        stem = m.group(1).strip()
        if stem and stem not in seen:
            seen.add(stem)
            out.append(stem)
    return out


# ── Closed-set construction ────────────────────────────────────────────────
# The rendered vault context labels each note as a `### [[Note-Name]]`
# header (both the abstract L0/L1/L2 path and the legacy
# build_graph_context path use this format). We parse those headers to
# build the allowed-citations set. We ALSO accept the raw search_results
# list as a fallback so we never miss a seed note that didn't get a header
# (e.g., if the context was budgeted/truncated).

_HEADER_RE = re.compile(r"^###\s*\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", re.MULTILINE)


def build_allowed_citations(
    context_str: str,
    search_results: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, str]]:
    """Build the {note_stem: {file_path, snippet}} closed-set from context.

    `context_str` is the rendered VAULT CONTEXT block. `search_results` is
    the raw FUSED retrieval list (each item has `file_path` + `content`).
    The two sources are merged; duplicates (same stem) keep the first
    file_path seen.
    """
    out: dict[str, dict[str, str]] = {}
    # 1. Headers from the rendered context — the authoritative source.
    for m in _HEADER_RE.finditer(context_str or ""):
        stem = m.group(1).strip()
        if stem and stem not in out:
            out[stem] = {"file_path": "", "snippet": ""}
    # 2. Raw search results — fill in file_path + snippet for any stem we
    #    can match, and add any seed that didn't render a header.
    if search_results:
        for r in search_results:
            if not isinstance(r, dict):
                continue
            fp = r.get("file_path", "")
            stem = ""
            try:
                from pathlib import Path

                if fp:
                    stem = Path(fp).stem
            except Exception:  # noqa: BLE001 — best-effort
                stem = ""
            if not stem:
                continue
            snippet = (r.get("content", "") or "")[:300]
            if stem in out:
                # Header existed; backfill file_path/snippet if missing.
                if not out[stem].get("file_path"):
                    out[stem]["file_path"] = fp
                if not out[stem].get("snippet"):
                    out[stem]["snippet"] = snippet
            else:
                out[stem] = {"file_path": fp, "snippet": snippet}
    return out


def add_citation_target(
    allowed: dict[str, dict[str, str]],
    file_path: str,
    snippet: str = "",
) -> dict[str, dict[str, str]]:
    """Register a note as a valid citation target (mid-loop tool retrieval).

    Returns the (mutated) `allowed` dict for convenience. Idempotent — a
    note already in the set is not overwritten (the preflight snippet
    wins, since it came from the curated context).
    """
    if not file_path:
        return allowed
    try:
        from pathlib import Path

        stem = Path(file_path).stem
    except Exception:  # noqa: BLE001 — best-effort
        return allowed
    if not stem:
        return allowed
    if stem not in allowed:
        allowed[stem] = {"file_path": file_path, "snippet": (snippet or "")[:300]}
    return allowed


# ── Grounding score ───────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """Split `text` into sentences on `.!?` followed by whitespace.

    Keeps it simple — this is a heuristic gate, not a parser. Short answers
    (<3 sentences after splitting) are not subject to the per-sentence
    threshold (see TUNABLES.ungrounded_sentence_threshold).
    """
    # Drop the trailing grounding-caution block we may have appended in a
    # prior pass so it doesn't count as an "uncited sentence".
    text = re.sub(r"\n\n>\s*⚠️.*$", "", text, flags=re.DOTALL).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def score_grounding(
    answer: str,
    allowed: dict[str, dict[str, str]] | None,
    graph_lookup=None,
) -> dict[str, Any]:
    """Score how grounded `answer` is against the closed set.

    Returns a dict with:
      - total_wikilinks: count of unique [[wikilinks]] in the answer
      - allowed_cited: how many of those are in the closed set
      - missing_from_set: wikilinks NOT in the closed set (may be hallucinated
        or from model weights)
      - missing_from_vault: wikilinks in the set but the note doesn't exist
        in the graph (broken citations) — only populated if graph_lookup
        is provided (callable: graph_lookup(stem) -> bool)
      - sentences: total sentences
      - ungrounded_sentences: sentences with NO [[wikilink]] from the closed set
      - ungrounded_ratio: ungrounded_sentences / sentences (0.0 if no sentences)
      - grounding_score: allowed_cited / total_wikilinks (1.0 if 0 wikilinks
        AND 0 sentences, i.e., trivially fine; 0.0 if 0 wikilinks but there
        ARE sentences — completely ungrounded)
      - failed: True if the answer should trigger a grounding retry
        (zero wikilinks with >1 sentence, OR ungrounded_ratio above the
        threshold on a >3-sentence answer)
    """
    links = extract_wikilinks(answer)
    total = len(links)
    allowed = allowed or {}

    allowed_cited = 0
    missing_from_set: list[str] = []
    for wl in links:
        if wl in allowed:
            allowed_cited += 1
        else:
            missing_from_set.append(wl)

    missing_from_vault: list[str] = []
    if graph_lookup is not None:
        for wl in links:
            # Only check ones that ARE in the allowed set; if the model
            # invented a wikilink not in the set, that's already counted
            # in missing_from_set.
            if wl in allowed and not graph_lookup(wl):
                missing_from_vault.append(wl)

    sentences = _split_sentences(answer)
    n_sent = len(sentences)
    allowed_stems = set(allowed.keys())
    ungrounded_sentences = 0
    for s in sentences:
        s_links = set(extract_wikilinks(s))
        if not (s_links & allowed_stems):
            ungrounded_sentences += 1
    ungrounded_ratio = (ungrounded_sentences / n_sent) if n_sent else 0.0

    if total == 0:
        grounding_score = 0.0 if n_sent > 1 else 1.0
    else:
        grounding_score = allowed_cited / total

    threshold = TUNABLES.ungrounded_sentence_threshold
    failed = False
    if total == 0 and n_sent > 1:
        failed = True
    elif n_sent > 3 and ungrounded_ratio > threshold:
        failed = True

    return {
        "total_wikilinks": total,
        "allowed_cited": allowed_cited,
        "missing_from_set": missing_from_set[:10],
        "missing_from_vault": missing_from_vault[:10],
        "sentences": n_sent,
        "ungrounded_sentences": ungrounded_sentences,
        "ungrounded_ratio": round(ungrounded_ratio, 2),
        "grounding_score": round(grounding_score, 2),
        "failed": failed,
    }


# ── Reprimand ─────────────────────────────────────────────────────────────

def build_reprimand(score: dict[str, Any], allowed: dict[str, dict[str, str]] | None) -> str:
    """Build the user-role message sent back to the model on a grounding fail.

    Lists the allowed citation targets so the model can re-cite, and states
    the rule plainly.
    """
    allowed = allowed or {}
    stems = list(allowed.keys())[:25]
    stems_block = ", ".join(f"[[{s}]]" for s in stems) if stems else "(no notes were retrieved — say 'I don't know' and offer to research)"
    missing = score.get("missing_from_set", [])
    missing_block = ""
    if missing:
        missing_block = (
            f"\nThe following [[wikilinks]] in your answer are NOT in the "
            f"allowed set (they may be from your weights, not the vault): "
            f"{', '.join(f'[[{m}]]' for m in missing[:8])}. Remove or replace them."
        )
    return (
        "# GROUNDING CHECK FAILED\n"
        "Your answer has uncited or ungrounded claims. You are a synthesis "
        "router — your world knowledge is DISABLED in this vault. You may "
        "ONLY make claims supported by notes in the VAULT CONTEXT, cited "
        "inline as [[Note-Name]] next to each claim.\n\n"
        f"Grounding score: {score.get('grounding_score', 0.0)} "
        f"({score.get('allowed_cited', 0)}/{score.get('total_wikilinks', 0)} "
        f"citations allowed). "
        f"{score.get('ungrounded_sentences', 0)}/{score.get('sentences', 0)} "
        f"sentences had no vault citation.\n\n"
        f"Notes you ARE allowed to cite: {stems_block}\n"
        f"{missing_block}\n\n"
        "Rewrite your answer. Every factual sentence MUST contain at least "
        "one [[wikilink]] from the allowed set above. If you cannot support "
        "a claim from those notes, say \"I don't know — nothing in the vault "
        "covers this\" and offer to call vault_research. Do NOT write from "
        "your own knowledge."
    )