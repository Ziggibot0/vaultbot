"""Abstracted graph context for the chat loop — replaces the old
`build_graph_context` content dump with a multi-resolution view.

The problem this solves
-----------------------
`build_graph_context` (vault_graph.py) walked the L0 link graph and
dumped `node["content"][:2_000]` for every connected note. With 5 seeds
× depth 2 that's 20+ notes × 2000 chars = 40K+ chars of first-2000
chunks flooding the LLM context — most of it low-density detail, and
~90% of each note invisible past the 2000 cut. The model saw MORE
context but LESS of each note's actual content.

The fix: a three-resolution context, biomimetic cortical hierarchy:

  - L2 (bird's-eye)  : the MOC of the top seed's cluster — ~500 chars,
                       orients the model to the topic neighborhood.
  - L1 (highway)     : terse concept cards for ALL walked nodes —
                       ~300-500 chars each, the hop-able graph the model
                       actually reasons over. 20 cards ≈ 6-10K chars.
  - L0 (drill-down)  : the raw content of the single top seed, capped at
                       DRILL_CAP (12000 chars) — far above the old 2000-char
                       legacy cut, with a pointer to the full section on
                       disk if it exceeds the cap. The model gets the one
                       note that matters most, nearly verbatim.

Net context drops ~4-6× while the model sees MORE of the vault's shape
(the L1 highway) AND more of the one note that counts (the L0 drill-down).
No content is lost to truncation — the full note is on disk, reachable
via the L1 `> source: [[...]]` pointer (the L0 context itself is capped
at DRILL_CAP = 12000 chars, so notes longer than that carry an appended
`[... full section on disk ...]` marker rather than being lost).

Degradation
-----------
If L1 cards don't exist yet (pre-hierarchy vault, or a non-textbook
note), falls back to the old `build_graph_context` behavior so the
chat loop never breaks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from concept_card import card_path_for, is_card, l0_path_for_card
from config import TUNABLES
from moc_builder import MOC_PREFIX
from vault_graph import VaultGraph, build_graph_context

# Simple stop words for keyword extraction — same set as
# small_model_filters._STOP_WORDS, duplicated here to keep this
# leaf module decoupled.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "to",
        "of",
        "in",
        "on",
        "at",
        "and",
        "or",
        "it",
        "this",
        "that",
        "for",
        "with",
        "as",
        "by",
        "its",
        "has",
        "have",
        "from",
        "which",
        "not",
        "but",
        "can",
        "will",
        "do",
        "does",
        "did",
        "you",
        "your",
        "we",
        "our",
        "they",
        "their",
        "he",
        "she",
    }
)


def _content_words(text: str) -> set[str]:
    """Extract content words (non-stop-words, >2 chars) for keyword overlap."""
    return {
        w.lower()
        for w in re.split(r"\s+", text)
        if w.lower() not in _STOP_WORDS and len(w) > 2
    }


def _read(path: str | Path) -> str:
    """Read a file as UTF-8, returning '' on any error (never raises)."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return ""


def _card_stem_to_l0_stem(card_stem: str) -> str:
    """Strip the `-L1` suffix from a card stem to get its L0 stem."""
    if card_stem.endswith("-L1"):
        return card_stem[:-3]
    return card_stem


def _moc_for_cluster(textbooks_dir: Path, cluster_id: str) -> Path | None:
    """Return the MOC note path for a cluster id, or None if it doesn't exist."""
    p = textbooks_dir / f"{MOC_PREFIX}{cluster_id}.md"
    if p.exists():
        return p
    return None


def _extract_cluster_id(card_text: str) -> str | None:
    """Pull the `> cluster: [[moc-<id>]]` cluster id out of a card's text."""
    m = re.search(
        "> cluster: \\[\\[" + re.escape(MOC_PREFIX) + "([^\\]]+)\\]\\]", card_text
    )
    if m:
        return m.group(1).strip()
    return None


def build_abstract_context(
    graph: VaultGraph,
    search_results: list[dict[str, Any]],
    query: str,
    k: int = 5,
    depth: int = 2,
    textbooks_dir: str | Path | None = None,
) -> dict[str, str]:
    """Build a multi-resolution context string for the chat loop.

    Returns {"context": str, "drill_down_used": bool, "resolution": str,
             "l1_cards": int, "l0_drill": Optional[str]}.

    Falls back to the legacy `build_graph_context` content dump when no
    L1 concept cards exist yet (pre-hierarchy vault regions, or a
    non-textbook note that has no card). Never raises — any failure
    degrades to the legacy builder (or a stub string if that also fails).
    """
    if textbooks_dir is None:
        textbooks_dir = Path(graph.vault_path) / "vaultbot/Knowledge/Textbooks"
    tdir = Path(textbooks_dir)

    # ---- Collect seed names + their L0 paths from the search results ----
    seeds: list[str] = []
    seed_l0_paths: list[Path] = []
    for res in search_results[:k]:
        fp = Path(res.get("file_path", ""))
        if not fp.exists():
            continue
        stem = fp.stem
        if is_card(fp):
            # A card seed: walk the card itself + its L0 section.
            seeds.append(stem)
            l0 = l0_path_for_card(fp)
            if l0:
                seed_l0_paths.append(l0)
        else:
            card = card_path_for(fp)
            if card.exists():
                # An L0 note that HAS a card: seed both the card + the L0.
                seeds.append(card.stem)
                seed_l0_paths.append(fp)
            else:
                # An L0 note with no card (pre-hierarchy): seed the L0 only.
                seeds.append(stem)
                seed_l0_paths.append(fp)

    if not seeds:
        # No usable seeds — fall back to the legacy builder.
        return {
            "context": build_graph_context(graph, search_results, query, k, depth),
            "drill_down_used": False,
            "resolution": "legacy",
            "l1_cards": 0,
            "l0_drill": None,
        }

    # ---- Walk the link graph from the seeds ----
    subgraph = graph.walk(seeds, depth=depth)

    # ---- Partition the walked nodes into L1 cards, L0 notes, and MOCs ----
    l1_cards: list[dict[str, Any]] = []
    l0_notes: list[dict[str, Any]] = []
    mocs: list[dict[str, Any]] = []
    for node in subgraph["nodes"]:
        node["name"]
        fp = Path(node["file_path"])
        if is_card(fp):
            l1_cards.append(node)
            continue
        if fp.stem.startswith(MOC_PREFIX):
            mocs.append(node)
            continue
        # An L0 note — if it has a co-located card, also surface the card
        # as an L1 highway entry so the model can hop to it.
        l0_notes.append(node)
        card = card_path_for(fp)
        if card.exists():
            l1_cards.append(
                {
                    "name": card.stem,
                    "file_path": str(card),
                    "content": _read(card),
                    "outgoing_links": node.get("outgoing_links", []),
                    "backlinks": node.get("backlinks", []),
                }
            )

    # ---- Make sure the top seed's card is in the highway ----
    seen_card_names = {c["name"] for c in l1_cards}
    for l0p in seed_l0_paths:
        card = card_path_for(l0p)
        if card.exists() and card.stem not in seen_card_names:
            l1_cards.append(
                {
                    "name": card.stem,
                    "file_path": str(card),
                    "content": _read(card),
                    "outgoing_links": [],
                    "backlinks": [],
                }
            )
            seen_card_names.add(card.stem)

    if not l1_cards:
        # No cards anywhere — pre-hierarchy vault. Use the legacy builder.
        return {
            "context": build_graph_context(graph, search_results, query, k, depth),
            "drill_down_used": False,
            "resolution": "legacy",
            "l1_cards": 0,
            "l0_drill": None,
        }

    # ---- Relevance-prune: cap the highway at max_files_in_context ----
    # The total files in context = L1 cards + 1 MOC + 1 L0 drill-down.
    # Reserve 2 slots for the MOC and L0 drill-down so the L1 highway gets
    # max_files_in_context - 2 cards.  This guarantees the TOTAL distinct
    # files shown to the model never exceeds TUNABLES.max_files_in_context,
    # regardless of how many seeds FUSED returned or how large the graph
    # walk is.
    MAX_CARDS_IN_CONTEXT = max(1, TUNABLES.max_files_in_context - 2)
    if len(l1_cards) > MAX_CARDS_IN_CONTEXT:
        # Score each card by its OWN keyword overlap with the query —
        # NOT by which seed it came from.  A card 2 hops from seed #1
        # about a different topic should NOT beat a highly relevant card
        # from seed #5.  The FUSED score (from search_results) is used
        # as a tiebreaker when keyword overlap is equal.
        query_words = _content_words(query)
        # Build a stem → FUSED score map for tiebreaking.
        fused_by_stem: dict[str, float] = {}
        for r in search_results:
            fp = r.get("file_path", "")
            if fp:
                fused_by_stem[Path(fp).stem] = r.get("score", 0.0)

        def card_score(c: dict[str, Any]) -> tuple[int, float]:
            """(keyword_overlap_count, fused_score) — higher is better."""
            name = c["name"]
            body = c.get("content", "")
            # Score by how many query content words appear in the card's
            # title + body (case-insensitive).  This is the same keyword-
            # overlap approach used by filter_context, but applied BEFORE
            # the cap so relevant cards survive the cut.
            card_text = (name + " " + body[:500]).lower()
            card_words = _content_words(card_text)
            overlap = len(query_words & card_words) if query_words else 0
            # Tiebreaker: FUSED score.  Resolve the card stem to its L0
            # stem for lookup (cards end in -L1).
            l0_stem = name[:-3] if name.endswith("-L1") else name
            fused = fused_by_stem.get(name, fused_by_stem.get(l0_stem, 0.0))
            return (overlap, fused)

        l1_cards.sort(key=card_score, reverse=True)
        l1_cards = l1_cards[:MAX_CARDS_IN_CONTEXT]

    # ---- Pick the top card + its MOC (L2 bird's-eye) ----
    top_card_text = _read(search_results[0]["file_path"]) if search_results else ""
    if search_results and not is_card(Path(search_results[0]["file_path"])):
        # The top hit is an L0 — prefer its co-located card for the L2 lookup.
        top_card = card_path_for(seed_l0_paths[0]) if seed_l0_paths else None
        if top_card and top_card.exists():
            top_card_text = _read(top_card)
    else:
        top_card = card_path_for(seed_l0_paths[0]) if seed_l0_paths else None
        if top_card and top_card.exists():
            top_card_text = _read(top_card)

    cluster_id = _extract_cluster_id(top_card_text)
    moc_text = ""
    if cluster_id:
        moc_path = _moc_for_cluster(tdir, cluster_id)
        if moc_path:
            moc_text = _read(moc_path)

    # ---- Render each L1 card as a terse, hop-able highway entry ----
    CARD_RENDER_CAP = 500
    card_lines: list[str] = []
    for node in l1_cards:
        body = node["content"]
        # Strip the H1, the `> source/cluster` pointer lines, and any
        # vaultbot HTML markers so the sketch is just the concept prose.
        sketch = re.sub("^# .*\\n", "", body, flags=re.MULTILINE)
        sketch = re.sub("^> .*\\n", "", sketch, flags=re.MULTILINE)
        sketch = re.sub("<!-- vaultbot:.*?-->\\n", "", sketch)
        sketch = sketch.strip()
        if len(sketch) > CARD_RENDER_CAP:
            sketch = sketch[:CARD_RENDER_CAP].rsplit(" ", 1)[0] + "…"
        links_out = node.get("outgoing_links", []) or []
        link_str = ", ".join(f"[[{n}]]" for n in links_out[:6])
        card_lines.append(
            f"### [[{node['name']}]]\n{sketch}"
            + (f"\nLinks: {link_str}" if link_str else "")
        )

    # ---- L0 drill-down: full raw of the top seed + 2nd/3rd seeds ----
    # The top seed gets the full DRILL_CAP (12000 chars). Seeds 2-3 get a
    # smaller extra drill (TUNABLES.abstract_extra_drill_cap, 4000) so
    # multi-note synthesis isn't limited to one note's body. The model
    # can synthesize accurately from 2-3 notes, not just cite the top one.
    drill_path: Path | None = None
    if seed_l0_paths:
        drill_path = seed_l0_paths[0]
    if not drill_path and l1_cards:
        drill_path = l0_path_for_card(l1_cards[0]["file_path"])
    drill_text = ""
    if drill_path and drill_path.exists():
        drill_text = _read(drill_path)
        DRILL_CAP = 12000
        if len(drill_text) > DRILL_CAP:
            drill_text = drill_text[:DRILL_CAP] + (
                f"\n\n*[... full section on disk: {drill_path.name} ...]*"
            )

    # Extra drill-downs for seeds 2-3 (multi-note synthesis surface).
    extra_drills: list[str] = []
    EXTRA_DRILL_CAP = TUNABLES.abstract_extra_drill_cap
    for _extra_path in seed_l0_paths[1:3]:
        if _extra_path == drill_path or not _extra_path.exists():
            continue
        _extra_text = _read(_extra_path)
        if not _extra_text:
            continue
        if len(_extra_text) > EXTRA_DRILL_CAP:
            _extra_text = _extra_text[:EXTRA_DRILL_CAP] + (
                f"\n\n*[... full section on disk: {_extra_path.name} ...]*"
            )
        extra_drills.append(f"### [[{_extra_path.stem}]] (drill-down)\n{_extra_text}")

    # ---- Assemble the multi-resolution context ----
    lines = [
        "VAULT CONTEXT — multi-resolution (L2 MOC + L1 cards + L0 drill-down)",
        f"Query: {query}",
        f"Graph stats: {subgraph['stats']['selected']} connected nodes from "
        f"{subgraph['stats']['seeds']} seed(s), depth {subgraph['stats']['depth']}.",
        f"Resolution: {len(l1_cards)} L1 cards, {len(mocs)} MOCs, 1 L0 drill-down "
        f"({drill_path.stem if drill_path else 'none'}).",
        "",
    ]
    if moc_text:
        lines.append("--- L2: MAP OF CONTENT (bird's-eye) ---")
        if len(moc_text) > 1200:
            moc_text = moc_text[:1200] + "…"
        lines.append(moc_text)
        lines.append("")
    lines.append("--- L1: CONCEPT CARDS (the thought highway — hop these) ---")
    lines.extend(card_lines)
    lines.append("")
    if drill_text:
        lines.append(f"--- L0: DRILL-DOWN (full raw of [[{drill_path.stem}]]) ---")
        lines.append(drill_text)
    else:
        lines.append(
            "--- L0: DRILL-DOWN (none — query the source via the card's "
            "> source link if needed) ---"
        )
    if extra_drills:
        lines.append("")
        lines.append("--- L0: EXTRA DRILL-DOWNS (seeds 2-3) ---")
        lines.extend(extra_drills)
    lines.append("")
    lines.append(
        "NOTE: L1 cards are terse summaries. For any card you need full "
        "detail on, follow its `> source: [[...]]` link (the drill-down "
        "above is the top hit only)."
    )

    return {
        "context": "\n".join(lines),
        "drill_down_used": drill_text != "",
        "resolution": "abstract",
        "l1_cards": len(l1_cards),
        "l0_drill": drill_path.stem if drill_path else None,
    }
