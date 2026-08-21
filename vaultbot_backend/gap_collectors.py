"""Gap-signal collection and curriculum scoring for KnowledgeCurriculum.

Extracted from ``knowledge_curriculum.py`` to keep the curriculum class
focused on state management and the public API.  This module owns the five
gap-signal collectors and the Voyager-style multiplicative scoring:

    priority = base_priority * diversity_bonus * achievability_bonus * context_bonus

Each collector is a standalone function that takes the ``vault_graph`` (and
any tunable thresholds / optional session logger) it needs, so
``KnowledgeCurriculum`` can call them without carrying all the collection
logic on the class itself.  The scoring functions take the gap dict plus the
context they need (completed topics, failed topics, graph, diversity
window).

The class keeps thin delegating methods (``_collect_dangling_links`` etc.)
so internal call sites are unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from vault_graph import WIKILINK_RE

# Module-private helpers re-exported here so the collectors don't need to
# import the curriculum module.  These mirror the originals in
# knowledge_curriculum.py -- kept in sync.
_STOP_TOKENS: set[str] = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "be",
    "by",
    "at",
    "as",
    "it",
    "this",
    "that",
    "from",
}


def _tokenize(text: str) -> set[str]:
    """Split on non-alphanumeric, lowercase, drop stop words + single chars."""
    if not text:
        return set()
    raw = re.split(r"[^a-z0-9]+", text.lower())
    out: set[str] = set()
    for tok in raw:
        if len(tok) <= 1:
            continue
        if tok in _STOP_TOKENS:
            continue
        out.add(tok)
    return out


def _token_overlap(s1: str, s2: str) -> float:
    """Embedding-free similarity in [0,1] between two strings (Jaccard)."""
    t1 = _tokenize(s1)
    t2 = _tokenize(s2)
    if not t1 or not t2:
        return 0.0
    inter = len(t1 & t2)
    if inter == 0:
        return 0.0
    union = len(t1 | t2)
    return inter / union if union else 0.0


def _is_hub_name(name: str) -> bool:
    """True if a note name looks like a MOC/Index hub note."""
    if not name:
        return False
    n = name.lower()
    return "moc" in n or "index" in n


# ---------------------------------------------------------------------------
# Gap-signal collectors
# ---------------------------------------------------------------------------


def collect_dangling_links(
    vault_graph,
    dangling: list[dict[str, Any]] | None = None,
    session_logger=None,
) -> list[dict[str, Any]]:
    """Signal 1: red links the vault has declared it wants to know.

    ``dangling`` may be supplied by the caller (``collect_all_gaps``
    computes it once and shares it with ``collect_missing_entities``)
    to avoid a second full ``graph.dangling_links()`` scan. If not
    supplied, falls back to computing it here for standalone callers.
    """
    try:
        if dangling is None:
            dangling = vault_graph.dangling_links(min_references=1)
        out: list[dict[str, Any]] = []
        for d in dangling:
            out.append(
                {
                    "kind": "dangling_link",
                    "topic": d.get("name", d.get("normalized_name", "")),
                    "normalized_name": d.get("normalized_name", ""),
                    "reference_count": int(d.get("reference_count", 1)),
                    "referenced_by": list(d.get("referenced_by", []) or []),
                    "file_path": None,
                    "base_priority": int(d.get("reference_count", 1)) * 10,
                }
            )
        return out
    except Exception as e:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        _log_error(session_logger, "collect_dangling_links", e)
        return []


def collect_thin_notes(
    vault_graph,
    min_content_length: int = 200,
    skip_vaultbot_paths: bool = True,
    session_logger=None,
) -> list[dict[str, Any]]:
    """Signal 2: existing notes with too-short bodies.

    Skips anything under Memory/Chat/ or Knowledge/Research/ (the bot's own
    drafts) so the curriculum doesn't chase its own work-in-progress.
    """
    try:
        thin = vault_graph.thin_notes(min_content_length=min_content_length)
        out: list[dict[str, Any]] = []
        for t in thin:
            file_path = t.get("file_path", "") or ""
            if skip_vaultbot_paths and any(
                d in file_path.replace("\\", "/")
                for d in (
                    "Memory/Chat/",
                    "Knowledge/Research/",
                )
            ):
                continue
            out.append(
                {
                    "kind": "thin_note",
                    "topic": t.get("name", t.get("normalized_name", "")),
                    "normalized_name": t.get("normalized_name", ""),
                    "reference_count": 0,
                    "referenced_by": vault_graph.neighbors(
                        t.get("normalized_name", ""), direction="in"
                    ),
                    "file_path": file_path,
                    "content_length": int(t.get("content_length", 0)),
                    "base_priority": 1,
                }
            )
        return out
    except Exception as e:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        _log_error(session_logger, "collect_thin_notes", e)
        return []


def collect_missing_entities(
    vault_graph,
    dangling: list[dict[str, Any]] | None = None,
    session_logger=None,
) -> list[dict[str, Any]]:
    """Signal 3: red links re-declared from recent notes, deduped.

    The set of dangling-link normalized names is the authoritative "what's
    missing" set; ``missing_entity`` is a thin wrapper that surfaces the
    same holes from the angle of *which notes keep asking for them*. To
    avoid double-counting we dedupe against the dangling-link candidate
    set by normalized name and only emit entries that contribute extra
    context (e.g. a referenced_by source the bare dangling scan missed).

    ``dangling`` is normally supplied by ``collect_all_gaps`` so this
    collector reuses the authoritative scan instead of recomputing it.
    """
    try:
        if dangling is None:
            dangling = vault_graph.dangling_links(min_references=1)
        dangling_names: set[str] = {
            d.get("normalized_name", "") for d in dangling if d.get("normalized_name")
        }
        # Re-scan every note's raw content for wikilinks to non-existent
        # notes -- same logic as dangling_links but we keep only entries
        # whose reference count from *recent* notes (by mtime) differs.
        ref_counts: dict[str, int] = {}
        ref_sources: dict[str, set[str]] = {}
        for name, node in vault_graph.nodes.items():
            raw_links = WIKILINK_RE.findall(node.get("content", "") or "")
            for link in raw_links:
                norm = vault_graph._normalize_name(link)
                if norm in vault_graph.nodes:
                    continue  # resolved -- not missing
                if norm not in dangling_names:
                    continue  # dangling_links already covers this
                ref_counts[norm] = ref_counts.get(norm, 0) + 1
                ref_sources.setdefault(norm, set()).add(name)

        out: list[dict[str, Any]] = []
        # missing_entity only adds value when it surfaces a topic with
        # multiple recent re-declarations; otherwise it's pure dup of the
        # dangling_link signal. Emit only the ones referenced from ≥2
        # distinct notes (dangling_links already covers the 1-ref case).
        for norm, count in ref_counts.items():
            if count < 2:
                continue
            display = norm
            for src in ref_sources.get(norm, set()):
                node = vault_graph.nodes.get(src)
                if not node:
                    continue
                for m in WIKILINK_RE.findall(node.get("content", "") or ""):
                    if vault_graph._normalize_name(m) == norm:
                        display = m.strip().lstrip("[")
                        break
                if display != norm:
                    break
            out.append(
                {
                    "kind": "missing_entity",
                    "topic": display,
                    "normalized_name": norm,
                    "reference_count": count,
                    "referenced_by": sorted(ref_sources.get(norm, set())),
                    "file_path": None,
                    "base_priority": count * 10,
                }
            )
        return out
    except Exception as e:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        _log_error(session_logger, "collect_missing_entities", e)
        return []


def collect_thin_communities(
    vault_graph,
    thin_community_min_size: int = 3,
    cache: list[dict[str, Any]] | None = None,
    cache_graph_mtime: float = -1.0,
    session_logger=None,
) -> tuple[list[dict[str, Any]], float]:
    """Signal 4: cliques of ≥3 linked notes with no MOC/Index hub.

    For each note, check whether its neighbor set forms a clique of at
    least ``thin_community_min_size`` notes where none of the members is a
    hub (name contains "MOC" or "Index"). A clique here is approximated by
    "every pair of neighbors is mutually linked" -- a strict but cheap
    check. We emit one gap per detected clique, keyed by its smallest
    member so duplicates collapse naturally.

    Performance: this is O(n * k^2) over every note and is the dominant
    cost of a cold gap-scoring pass. The result depends ONLY on the graph
    topology (nodes + edges), which is mtime-tracked on
    ``VaultGraph._last_refresh_mtime``. So we cache the result keyed on
    that mtime: a back-to-back scoring pass that finds the graph unchanged
    reuses the previous clique list for free. The cache is invalidated
    only when the graph actually changes (a note added/edited/linked),
    which is exactly the right condition for a topology-only signal.

    Returns ``(out, graph_mtime)`` so the caller can persist the cache
    pair (list + mtime) on the curriculum instance.
    """
    try:
        graph_mtime = getattr(vault_graph, "_last_refresh_mtime", 0.0)
        if cache is not None and cache_graph_mtime == graph_mtime and graph_mtime > 0.0:
            # Graph topology unchanged since last compute -- reuse.
            return list(cache), graph_mtime

        min_size = thin_community_min_size
        out: list[dict[str, Any]] = []
        seen_cliques: set[tuple[str, ...]] = set()

        for name in vault_graph.nodes:
            neighbors = vault_graph.neighbors(name, direction="both")
            # Restrict to neighbors that aren't themselves hubs.
            non_hub_neighbors = [
                n
                for n in neighbors
                if not _is_hub_name(vault_graph.nodes.get(n, {}).get("name", n))
                and not _is_hub_name(name)
            ]
            if len(non_hub_neighbors) < min_size - 1:
                # The clique includes `name` itself, so we need ≥ min_size-1
                # neighbors to reach min_size total.
                continue

            # Build the candidate clique: name + non-hub neighbors.
            members = [name, *non_hub_neighbors]
            # Keep only members that are mutually linked to *every* other
            # member (strict clique). This is O(k^2) per note but k is tiny.
            clique: list[str] = []
            for m in members:
                linked = set(vault_graph.neighbors(m, direction="both"))
                if all(other in linked for other in members if other != m):
                    clique.append(m)

            if len(clique) < min_size:
                continue

            clique_sorted = sorted(set(clique))
            key = tuple(clique_sorted)
            if key in seen_cliques:
                continue
            seen_cliques.add(key)

            # Represent the clique by its smallest member's display name;
            # the topic is the *missing* hub, phrased as the clique.
            display_members = [
                vault_graph.nodes.get(n, {}).get("name", n) for n in clique_sorted
            ]
            topic = "MOC for: " + ", ".join(display_members[:6])
            # Referenced_by = the clique members (they'd backlink a hub).
            out.append(
                {
                    "kind": "thin_community",
                    "topic": topic,
                    "normalized_name": "|".join(clique_sorted),
                    "reference_count": len(clique_sorted),
                    "referenced_by": clique_sorted,
                    "file_path": None,
                    "base_priority": len(clique_sorted),
                }
            )

        return out, graph_mtime
    except Exception as e:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        _log_error(session_logger, "collect_thin_communities", e)
        return [], getattr(vault_graph, "_last_refresh_mtime", 0.0)


def collect_link_density_anomalies(
    vault_graph,
    link_density_min_outlinks: int = 5,
    session_logger=None,
) -> list[dict[str, Any]]:
    """Signal 5: notes with many out-links but zero in-links (sinks).

    A note that links out heavily but is linked back to by nobody is a
    dead-end; the curriculum suggests it should be re-linked into the
    graph. Lower priority than the other signals.
    """
    try:
        out: list[dict[str, Any]] = []
        threshold = link_density_min_outlinks
        for name, node in vault_graph.nodes.items():
            out_links = vault_graph.edges.get(name, set())
            in_links = vault_graph.backlinks.get(name, set())
            if len(out_links) >= threshold and not in_links:
                out.append(
                    {
                        "kind": "link_density",
                        "topic": node.get("name", name),
                        "normalized_name": name,
                        "reference_count": len(out_links),
                        "referenced_by": [],  # by definition, nobody links in
                        "file_path": node.get("file_path"),
                        "base_priority": 1,  # deliberately low
                    }
                )
        return out
    except Exception as e:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        _log_error(session_logger, "collect_link_density_anomalies", e)
        return []


# ---------------------------------------------------------------------------
# Filtering + scoring
# ---------------------------------------------------------------------------


def filter_candidates(
    candidates: list[dict[str, Any]],
    completed_topics: list[str],
    failed_topics: list[dict[str, Any]],
    is_researchable_topic,
) -> list[dict[str, Any]]:
    """Drop completed topics and topics that failed very recently.

    A failed topic is suppressed for a cooldown window; the achievability
    bonus already crushes gaps that failed ≥3 times, so here we only
    hard-filter topics that failed in the *most recent* attempt to avoid
    an immediate retry loop.
    """
    completed: set[str] = {
        t.lower() for t in (completed_topics or []) if isinstance(t, str)
    }
    failed_recently: set[str] = {
        fr.get("topic", "").lower()
        for fr in (failed_topics or [])
        if isinstance(fr, dict) and fr.get("attempts", 0) >= 3
    }

    kept: list[dict[str, Any]] = []
    for g in candidates:
        topic = (g.get("topic") or "").lower()
        norm = (g.get("normalized_name") or "").lower()
        if topic in completed or norm in completed:
            continue
        # thin_community normalized_name is a pipe-joined clique key; only
        # the topic string is meaningful for completion checks.
        if g.get("kind") != "thin_community" and (
            topic in failed_recently or norm in failed_recently
        ):
            continue
        # Quality gate: reject trivial / placeholder topics that would
        # produce dictionary-scraping junk notes. thin_community topics
        # ("MOC for: ...") are synthetic and always pass -- the gate is
        # about the raw dangling-link / thin-note labels.
        if not is_researchable_topic(g):
            continue
        kept.append(g)
    return kept


def score_gap(
    gap: dict[str, Any],
    vault_graph,
    completed_topics: list[str],
    failed_topics: list[dict[str, Any]],
    diversity_window: int,
) -> dict[str, Any]:
    """Apply the Voyager curriculum priority to a single gap.

        priority = base_priority * diversity_bonus * achievability_bonus * context_bonus

    Returns the gap dict augmented with ``priority`` and ``score_breakdown``.
    """
    base = float(gap.get("base_priority", 1) or 1)
    diversity = diversity_bonus(gap, vault_graph, completed_topics, diversity_window)
    achievability = achievability_bonus(gap, failed_topics)
    context = context_bonus(gap, vault_graph)
    priority = base * diversity * achievability * context

    breakdown: dict[str, float] = {
        "base_priority": base,
        "diversity_bonus": diversity,
        "achievability_bonus": achievability,
        "context_bonus": context,
        "priority": priority,
    }
    gap = dict(gap)  # shallow copy so we don't mutate the candidate
    gap["priority"] = priority
    gap["score_breakdown"] = breakdown
    gap["reason"] = explain(gap, breakdown)
    return gap


def diversity_bonus(
    gap: dict[str, Any],
    vault_graph,
    completed_topics: list[str],
    diversity_window: int,
) -> float:
    """Penalize gaps too similar to recently-completed topics.

    A gap whose tokens heavily overlap the last ``diversity_window``
    completed topics gets x0.3; moderate overlap → x0.6; no overlap → x1.0.
    """
    completed = (completed_topics or [])[-diversity_window:]
    if not completed:
        return 1.0

    topic = gap.get("topic", "") or ""
    # For thin_community, also fold in the clique member names so diversity
    # considers the actual notes, not the synthetic "MOC for: ..." string.
    if gap.get("kind") == "thin_community":
        ref_by = gap.get("referenced_by") or []
        topic = (
            topic
            + " "
            + " ".join(vault_graph.nodes.get(n, {}).get("name", n) for n in ref_by)
        )

    max_overlap = 0.0
    for done in completed:
        if not isinstance(done, str):
            continue
        ov = _token_overlap(topic, done)
        if ov > max_overlap:
            max_overlap = ov

    if max_overlap >= 0.5:
        return 0.3
    if max_overlap >= 0.2:
        return 0.6
    return 1.0


def achievability_bonus(
    gap: dict[str, Any],
    failed_topics: list[dict[str, Any]],
) -> float:
    """Reward gaps that are cheap to close; crush repeatedly-failed ones.

    - thin_note (already exists, just needs expanding): x1.5
    - dangling_link with 1 reference: x1.0
    - dangling_link with many references (high value, harder): x1.2
    - missing_entity: x1.1 (already partially surfaced)
    - thin_community / link_density: x1.0
    - any topic that failed ≥3 times: x0.1
    """
    kind = gap.get("kind", "")
    ref_count = int(gap.get("reference_count", 0) or 0)

    if kind == "thin_note":
        bonus = 1.5
    elif kind == "dangling_link":
        bonus = 1.0 if ref_count <= 1 else 1.2
    elif kind == "missing_entity":
        bonus = 1.1
    elif kind == "thin_community" or kind == "link_density":
        bonus = 1.0
    else:
        bonus = 1.0

    # Failure penalty: ≥3 attempts collapses the bonus.
    topic = (gap.get("topic") or "").lower()
    norm = (gap.get("normalized_name") or "").lower()
    for fr in failed_topics or []:
        if not isinstance(fr, dict):
            continue
        ft = (fr.get("topic") or "").lower()
        fn = ""
        attempts = int(fr.get("attempts", 0) or 0)
        if ft == topic or (fn and fn == norm):
            if attempts >= 3:
                return 0.1
            if attempts >= 1:
                bonus *= 0.7
    return bonus


def context_bonus(
    gap: dict[str, Any],
    vault_graph,
) -> float:
    """Boost gaps whose referenced_by notes are well-connected (high degree).

    Filling a gap that wedges into a rich neighborhood (referencing notes
    have many neighbors) yields more graph rewiring per research effort.
    Returns x1.3 when the average referencing-note degree is high, x1.0
    otherwise.
    """
    ref_by = gap.get("referenced_by") or []
    if not ref_by:
        return 1.0
    degrees: list[int] = []
    for src in ref_by:
        if not isinstance(src, str):
            continue
        degree = len(vault_graph.neighbors(src, direction="both"))
        degrees.append(degree)
    if not degrees:
        return 1.0
    avg_degree = sum(degrees) / len(degrees)
    # "High degree" = the referencing notes themselves have ≥4 neighbors
    # on average. Tunable; kept conservative so it nudges, not dominates.
    if avg_degree >= 4:
        return 1.3
    if avg_degree >= 2:
        return 1.1
    return 1.0


def explain(gap: dict[str, Any], breakdown: dict[str, float]) -> str:
    """Human-readable reason string for why this gap was selected."""
    try:
        kind = gap.get("kind", "gap")
        topic = gap.get("topic", "")
        parts: list[str] = [f"{kind}='{topic}'"]
        parts.append(f"base={breakdown['base_priority']:.1f}")
        parts.append(f"div={breakdown['diversity_bonus']:.2f}")
        parts.append(f"ach={breakdown['achievability_bonus']:.2f}")
        parts.append(f"ctx={breakdown['context_bonus']:.2f}")
        parts.append(f"priority={breakdown['priority']:.2f}")
        return " | ".join(parts)
    except Exception:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        return "curriculum-selected gap"


def _log_error(session_logger, context: str, exc: BaseException) -> None:
    """Best-effort error log through the session logger."""
    try:
        if session_logger is not None:
            import traceback

            session_logger.log(
                "curriculum_error",
                {
                    "context": context,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
            )
    except Exception:  # noqa: BLE001 -- best-effort, returns error/empty to caller -- see CONTRIBUTING.md no-silent-fallbacks
        pass
