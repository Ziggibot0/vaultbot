"""Concept cards — the L1 abstraction layer over raw textbook sections (L0).

Why
---
Raw ingested sections (L0) are verbatim chapters, often 2K-80K chars each.
At synthesis time the chat loop cannot stuff 20 of those into the LLM
context, so `build_graph_context` truncated each to its first 2000 chars
— silently losing ~90% of every note the model "saw." The fix is an
abstraction hierarchy: a terse, hop-able layer of concept cards (L1) that
the model walks instead of the raw sections, with a single drill-down to
L0 for the most relevant card only.

Biomimetic mapping
------------------
- Hippocampal index (Teyler & DiScenna, 1986): L1 cards don't store the
  content, they POINT into L0 via a `[[source-section]]` link. Thinking
  hops L1->L1; detail retrieval drills L1->L0 only when needed.
- Systems consolidation (rehearsal-gated): L1 starts as a cheap
  extractive sketch (zero LLM). Only cards retrieved 3+ times get
  LLM-refined into a tight semantic summary — same rehearsal pattern as
  the existing lazy_condenser. The brain doesn't consolidate every
  memory, only the rehearsed ones.

A concept card is a small markdown file `*-L1.md` sitting next to its L0
section, containing:

    # <heading>  (concept card)
    > source: [[<source-section>]]
    > cluster: [[moc-<cluster-id>]]   (filled in by moc_builder)

    <extractive sketch: heading + top TF-IDF sentences + key terms>

    ## Links out
    - [[<neighbor-section>]]
    - ...

It is a first-class vault node: indexed by FAISS, walked by the link
graph, hop-able by the LLM.  It is NOT a duplicate of L0 — it is an
index/pointer with just enough semantic content to route thinking.

LLM usage
---------
Zero at ingest (extractive sketch only).  One `chat()` call per card
that crosses the touch threshold, lazy, background, reusing the same
rehearsal contract as lazy_condenser.  See `refine_card`.
"""

from __future__ import annotations

import os
import re
import json
import math
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Markers (HTML comments so they're invisible in Obsidian render but
# machine-detectable).  Same convention as the textbook source-key +
# condenser markers.
CARD_MARKER = "<!-- vaultbot:concept-card -->"
REFINED_MARKER = "<!-- vaultbot:card-refined -->"
SOURCE_LINK_RE = re.compile(r"> source: \[\[([^\]]+)\]\]")
CLUSTER_LINK_RE = re.compile(r"> cluster: \[\[([^\]]+)\]\]")
WIKILINK_RE = re.compile(r"\[\[([^\][\|\r\n]+)(?:\|[^\]\r\n]+)?\]\]")

# Extractive-sketch sizing.  Cards aim for ~300-500 chars so the
# abstract-context step can show ~20 of them in one LLM turn (~6-10K
# chars) — the "thought highway" view.
SKETCH_MAX_CHARS = 600
SKETCH_TOP_SENTENCES = 3
SKETCH_TOP_TERMS = 8
MIN_SENTENCE_LEN = 40  # skip headings-as-sentences and stray list items

# Lazy-refine threshold (touches before an LLM rewrites the sketch).
# Matches the condenser's 3-touch contract so one tuning knob governs both.
REFINE_TOUCH_THRESHOLD = 3
REFINE_MIN_CHARS = 250  # don't bother refining an already-tight card


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def card_path_for(l0_abs_path: str | Path) -> Path:
    """The L1 card path for a given L0 section path.

    `<slug>.md` -> `<slug>-L1.md` in the same directory.  Co-located so the
    link graph and the watcher see them as neighbors.
    """
    p = Path(l0_abs_path)
    return p.with_name(p.stem + "-L1.md")


def l0_path_for_card(card_abs_path: str | Path) -> Optional[Path]:
    """Inverse of `card_path_for`: the L0 section a card points at.

    Falls back to reading the `> source: [[...]]` line if the naming
    convention has been broken.
    """
    p = Path(card_abs_path)
    l0 = p.with_name(p.stem[:-3] if p.stem.endswith("-L1") else p.stem + ".md")
    # ensure .md
    if not l0.suffix == ".md":
        l0 = l0.with_suffix(".md")
    if l0.exists():
        return l0
    # fall back to the embedded source link
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        m = SOURCE_LINK_RE.search(text)
        if m:
            # resolve via rglob in the same directory
            target = m.group(1).strip()
            cand = p.parent / (target + ".md")
            if cand.exists():
                return cand
            for q in p.parent.glob("*.md"):
                if q.stem.lower() == target.lower():
                    return q
    except Exception as e:
        logger.debug("swallowed: %s", e)
    return None


def is_card(path: str | Path) -> bool:
    p = Path(path)
    if not p.suffix == ".md":
        return False
    if not p.stem.endswith("-L1"):
        return False
    return True


# ---------------------------------------------------------------------------
# Extractive sketch (zero LLM)
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> List[str]:
    # Strip markdown headers / lists / blockquotes first; keep prose paragraphs.
    prose_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("#", ">", "-", "*", "+", "|", "```")):
            continue
        if s.startswith("!["):
            continue
        prose_lines.append(s)
    prose = " ".join(prose_lines)
    # Sentence split on . ! ? followed by space + capital.
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', prose)
    return [p.strip() for p in parts if len(p.strip()) >= MIN_SENTENCE_LEN]


def _tokenize(text: str) -> List[str]:
    return [w for w in re.findall(r"\b[a-z][a-z0-9-]{2,}\b", text.lower())
            if len(w) > 2]


def _top_tfidf_terms(sentences: List[str], top_n: int = SKETCH_TOP_TERMS) -> List[str]:
    """Cheap, corpus-local TF (no IDF corpus handy — use length-normalized TF
    with a stopword list).  Good enough for an extractive sketch."""
    STOP = {
        "the", "and", "for", "are", "was", "were", "but", "not", "you", "that",
        "this", "with", "from", "they", "have", "has", "had", "his", "her",
        "their", "there", "then", "than", "which", "who", "whom", "whose",
        "what", "when", "where", "why", "how", "will", "would", "could",
        "should", "may", "might", "must", "can", "into", "upon", "such",
        "very", "more", "most", "some", "any", "all", "both", "each", "other",
        "its", "it", "is", "be", "been", "being", "as", "at", "by", "an",
        "or", "if", "so", "do", "does", "did", "about", "above", "below",
        "between", "through", "during", "after", "before", "off", "over",
        "under", "again", "further", "once", "here", "your", "our", "we",
        "us", "them", "him", "she", "he", "i", "me", "my", "mine", "ours",
        "chapter", "section", "figure", "table", "example", "exercise",
        "note", "notes", "see", "shown", "shows", "using", "use", "used",
        "one", "two", "three", "first", "second", "third", "also", "than",
    }
    tf: Dict[str, int] = {}
    for s in sentences:
        for tok in _tokenize(s):
            if tok in STOP:
                continue
            tf[tok] = tf.get(tok, 0) + 1
    # score = tf * log(1 + 1/length_norm) — slight preference for terms
    # concentrated in few sentences (a poor-man's IDF).
    scored = sorted(tf.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in scored[:top_n]]


def _extractive_sketch(l0_text: str, heading: str = "") -> str:
    """Produce a zero-LLM terse sketch of an L0 section.

    Heading + top sentences (TF-ranked) + key terms.  Capped at
    SKETCH_MAX_CHARS.
    """
    sentences = _split_sentences(l0_text)
    if not sentences:
        # fallback: first 400 chars of stripped body
        body = re.sub(r'[\s\n]+', ' ', l0_text).strip()
        sketch = body[:SKETCH_MAX_CHARS]
    else:
        terms = _top_tfidf_terms(sentences)
        # rank sentences by sum of term-weights they contain
        term_set = set(terms)
        def sent_score(s: str) -> int:
            toks = set(_tokenize(s))
            return len(toks & term_set) + min(len(s), 200) / 200.0
        ranked = sorted(sentences, key=sent_score, reverse=True)
        # keep document order for the top picks (readability)
        top = sorted(ranked[:SKETCH_TOP_SENTENCES],
                     key=lambda s: sentences.index(s))
        sketch = " ".join(top)
        if terms:
            sketch += "\n\nKey terms: " + ", ".join(terms) + "."
        if len(sketch) > SKETCH_MAX_CHARS:
            sketch = sketch[:SKETCH_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return sketch.strip()


# ---------------------------------------------------------------------------
# Card construction (LLM-free)
# ---------------------------------------------------------------------------

def build_card_text(l0_path: Path, l0_text: str, outgoing_links: List[str]) -> str:
    """Compose the full markdown body of an L1 concept card.

    `outgoing_links` are the L0 section's outgoing wikilink targets (the
    L0 link graph).  The card inherits them so hopping on the L1 graph
    mirrors hopping on L0 — but at 1/100th the context cost.
    """
    heading = l0_path.stem.replace("-", " ").replace("L1", "").strip()
    # Try to lift a better heading from the L0 body's first H1/H2.
    m = re.search(r'^#{1,2}\s+(.+)$', l0_text, flags=re.MULTILINE)
    if m:
        heading = m.group(1).strip()
    sketch = _extractive_sketch(l0_text, heading=heading)
    body = [
        f"# {heading}  (concept card)",
        "",
        f"> source: [[{l0_path.stem}]]",
        f"> cluster: [[TODO]]",  # filled in by moc_builder
        "",
        CARD_MARKER,
        "",
        sketch,
        "",
    ]
    if outgoing_links:
        body.append("## Links out")
        for tgt in outgoing_links:
            body.append(f"- [[{tgt}]]")
        body.append("")
    return "\n".join(body)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build_card_for(l0_abs_path: str | Path,
                   l0_text: Optional[str] = None,
                   outgoing_links: Optional[List[str]] = None,
                   vault_graph: Any = None) -> Optional[Path]:
    """Create or refresh the L1 card for an L0 section.  Returns the card
    path, or None on failure.  Idempotent: re-running overwrites in place.

    If `vault_graph` is provided, `outgoing_links` is derived from it
    (preferred — picks up cross-book links the ingester didn't see).
    """
    try:
        p = Path(l0_abs_path).resolve()
        if not p.exists():
            return None
        if l0_text is None:
            l0_text = p.read_text(encoding="utf-8", errors="replace")
        if outgoing_links is None and vault_graph is not None:
            node = vault_graph.get_note(p.stem)
            outgoing_links = list(node.get("links", [])) if node else []
        elif outgoing_links is None:
            outgoing_links = sorted({m for m in WIKILINK_RE.findall(l0_text)})
        # Don't recursively link to our own card / source.
        outgoing_links = [t for t in outgoing_links
                          if not t.lower().endswith("-l1")
                          and t.lower() != p.stem.lower()]
        card = card_path_for(p)
        text = build_card_text(p, l0_text, outgoing_links)
        # Preserve a refined marker if the card was already LLM-refined —
        # don't clobber a good refinement with a fresh extractive sketch
        # unless the caller explicitly forces it.
        if card.exists():
            try:
                old = card.read_text(encoding="utf-8", errors="replace")
                if REFINED_MARKER in old and CARD_MARKER in old:
                    # already refined: keep it (refinement is sticky)
                    return card
            except Exception as e:
                logger.debug("swallowed: %s", e)
        _atomic_write(card, text)
        return card
    except Exception:
        return None


def build_cards_batch(l0_abs_paths: List[str],
                      vault_graph: Any = None,
                      progress_callback: Any = None) -> Dict[str, Any]:
    """Build L1 cards for many L0 sections.  LLM-free.

    Returns {"cards_built": int, "cards_skipped": int, "card_paths": [...]}.
    """
    built = 0
    skipped = 0
    out_paths: List[str] = []
    n = len(l0_abs_paths)
    for i, fp in enumerate(l0_abs_paths):
        try:
            p = Path(fp)
            if not p.exists() or is_card(p):
                skipped += 1
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            links: Optional[List[str]] = None
            if vault_graph is not None:
                node = vault_graph.get_note(p.stem)
                if node:
                    links = list(node.get("links", set()))
            card = build_card_for(fp, l0_text=text, outgoing_links=links,
                                  vault_graph=vault_graph)
            if card is not None:
                built += 1
                out_paths.append(str(card))
            else:
                skipped += 1
        except Exception:
            skipped += 1
        if progress_callback is not None and (i % 10 == 0 or i == n - 1):
            try:
                progress_callback("concept_card", {
                    "note": i + 1, "total": n,
                    "message": f"Abstracting note {i+1}/{n}..."})
            except Exception as e:
                logger.debug("swallowed: %s", e)
    return {"cards_built": built, "cards_skipped": skipped,
            "card_paths": out_paths}


# ---------------------------------------------------------------------------
# Lazy LLM refine (rehearsal-gated, like the condenser)
# ---------------------------------------------------------------------------

def needs_refine(card_path: str | Path, touch_count: int) -> bool:
    """True when a card has earned an LLM refine: rehearsed enough AND
    still in extractive (not refined) form AND not vanishingly short."""
    if touch_count < REFINE_TOUCH_THRESHOLD:
        return False
    try:
        text = Path(card_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    if REFINED_MARKER in text:
        return False  # already refined
    if len(text) < REFINE_MIN_CHARS:
        return False
    return True


def refine_card(card_path: str | Path,
                ollama_client: Any,
                l0_text: Optional[str] = None) -> Dict[str, Any]:
    """One-shot LLM rewrite of an extractive card into a tight semantic
    summary.  Preserves the header, the `> source:` pointer, the links,
    and the markers — only the sketch body is rewritten.  Idempotent.

    Returns {"refined": bool, "before_chars": int, "after_chars": int}.
    Never raises.
    """
    try:
        p = Path(card_path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if REFINED_MARKER in text:
            return {"refined": False, "reason": "already_refined"}
        # Drill into L0 for the source content.
        if l0_text is None:
            l0 = l0_path_for_card(p)
            if l0 is None or not l0.exists():
                return {"refined": False, "reason": "no_source"}
            l0_text = l0.read_text(encoding="utf-8", errors="replace")
        # Cap the L0 input to the model — 6K chars is plenty for a
        # one-paragraph abstraction; more would burn context for nothing.
        l0_excerpt = l0_text[:6000]

        prompt = (
            "Rewrite the following textbook section as a tight concept-card "
            "summary: 2-4 sentences capturing the core idea, definitions, "
            "and key formulas. Preserve every [[wikilink]] target verbatim. "
            "Do NOT include the heading, source pointer, or link list — only "
            "the summary prose. Drop pedagogical scaffolding and worked "
            "examples.\n\nSECTION:\n" + l0_excerpt)
        resp = ollama_client.chat(
            [{"role": "system",
              "content": "You are a concept-card writer. Be terse and dense."},
             {"role": "user", "content": prompt}],
            stream=False)
        summary = ""
        if isinstance(resp, dict):
            summary = (resp.get("message") or {}).get("content", "") or resp.get("content", "")
        summary = summary.strip()
        if len(summary) < 80:
            return {"refined": False, "reason": "too_short"}

        # Reassemble: header + pointer + markers + new summary + links.
        # Re-extract the links block from the old card.
        links_block = ""
        m = re.search(r"(## Links out\n.*)$", text, flags=re.DOTALL)
        if m:
            links_block = m.group(1)
        header_end = text.find(CARD_MARKER)
        header = text[:header_end] if header_end != -1 else ""
        new_body = (header + CARD_MARKER + "\n\n" + summary + "\n\n"
                    + REFINED_MARKER + "\n")
        if links_block:
            new_body += links_block.rstrip() + "\n"
        before = len(text)
        after = len(new_body)
        _atomic_write(p, new_body)
        return {"refined": True, "before_chars": before, "after_chars": after}
    except Exception as e:
        return {"refined": False, "reason": f"error:{type(e).__name__}"}