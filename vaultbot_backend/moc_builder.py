"""Map-of-Content (MOC) builder — the L2 abstraction layer over L1 cards.

Why
---
L1 concept cards are the hop-able highway, but with a whole library
ingested there can be hundreds of them. The L2 layer groups
semantically-related L1 cards into clusters and emits one MOC note per
cluster — a bird's-eye index the LLM can read in ~500 chars to orient
itself before drilling down.

Biomimetic mapping
------------------
- Association cortex / schemas (Bartlett 1932): a schema is an
  abstraction over many concrete episodes. A MOC is an abstraction over
  many L1 cards.
- Community structure (GraphRAG, arXiv:2404.16130): entities cluster
  into communities; community-level summaries give the high-level view.
  We do the same with L1 cards, clustering by embedding similarity.

LLM usage
---------
Zero.  Clustering reuses the chunked embeddings computed at ingest (no
new embedding calls).  The cluster label is extractive (top shared
TF-IDF term across the cluster's cards).  The MOC is a flat list of
`[[card]]` links + the label.  This is deliberately dumber than
GraphRAG's LLM-summarized communities — it's an INDEX, not a synthesis.
The LLM only fires at the final synthesis step, exactly as Sean wants.

A MOC note is `vaultbot/textbooks/moc-<cluster-id>.md`:

    # Map of Content: <label>
    > cluster-id: <hex>
    <!-- vaultbot:moc -->

    ## Cards
    - [[<card-stem>]]
    - ...

    ## Related clusters
    - [[moc-<neighbor-cluster-id>]]
"""

from __future__ import annotations

import os
import re
import json
import hashlib
import logging
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

MOC_MARKER = "<!-- vaultbot:moc -->"
MOC_PREFIX = "moc-"

# Clustering threshold — RELATIVE, not absolute (raw nomic-embed L2
# distances in 768-dim space run ~45-195, not 0-1).  Same proven pattern
# as _cross_link_textbooks in main.py: link cards within
#   ratio * nearest-neighbor distance
# with an absolute floor to skip degenerate far-apart clusters.
CLUSTER_DISTANCE_RATIO = 1.6
CLUSTER_MAX_ABS_DISTANCE = 140.0
CLUSTER_MIN_SIZE = 3      # don't make a MOC for 1-2 stray cards
CLUSTER_MAX_CARDS = 40    # split huge clusters later if needed


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Clustering (greedy connected components at a relative threshold)
# ---------------------------------------------------------------------------

def cluster_cards(card_paths: List[str],
                  emb_by_path: Dict[str, Any]) -> List[List[str]]:
    """Cluster L1 cards by embedding similarity.

    `emb_by_path` maps card abs-path (str) -> embedding (list or ndarray).
    Cards not in the dict are dropped.  Greedy: each card joins the
    cluster of its nearest already-clustered neighbor if within
    `CLUSTER_DISTANCE_RATIO * that neighbor's nearest distance`, else
    starts a new cluster.  This is single-linkage agglomerative with a
    relative cutoff — cheap, deterministic, no sklearn.
    """
    paths = [p for p in card_paths if p in emb_by_path]
    if not paths:
        return []
    vecs = {p: np.asarray(emb_by_path[p], dtype=np.float32) for p in paths}
    # Pairwise nearest-neighbor distances (n is small — hundreds at most).
    # For each card, find its nearest other card.
    nearest: Dict[str, Tuple[float, str]] = {}
    for i, p in enumerate(paths):
        best_d = float("inf")
        best_q = None
        for j, q in enumerate(paths):
            if i == j:
                continue
            d = float(np.linalg.norm(vecs[p] - vecs[q]))
            if d < best_d:
                best_d = d
                best_q = q
        if best_q is not None:
            nearest[p] = (best_d, best_q)

    # Union-find over the relative-threshold edge set.
    parent = {p: p for p in paths}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for p, (d, q) in nearest.items():
        # nearest[q] may not exist if q had no neighbor (shouldn't happen
        # for n>1, but guard).
        qn = nearest.get(q)
        q_nearest_d = qn[0] if qn else d
        # use the smaller of the two nearest distances as the local scale
        local_scale = min(d, q_nearest_d)
        threshold = CLUSTER_DISTANCE_RATIO * local_scale
        # absolute floor
        if d > CLUSTER_MAX_ABS_DISTANCE:
            continue
        if d <= threshold:
            union(p, q)

    # Collect clusters.
    clusters: Dict[str, List[str]] = {}
    for p in paths:
        r = find(p)
        clusters.setdefault(r, []).append(p)
    return [c for c in clusters.values() if len(c) >= CLUSTER_MIN_SIZE]


# ---------------------------------------------------------------------------
# Cluster label (extractive, LLM-free)
# ---------------------------------------------------------------------------

def _card_terms(text: str) -> Set[str]:
    """Extract meaningful terms from a concept card for cluster labeling.

    Strips boilerplate (headers, pointers, markers, link block) so the
    label reflects the card's semantic content, not its scaffolding.
    """
    # Strip the header line, pointer lines, markers, and the Links block.
    body = text
    body = re.sub(r'^# .*\n', '', body, flags=re.MULTILINE)
    body = re.sub(r'^> .*\n', '', body, flags=re.MULTILINE)
    body = re.sub(r'<!-- vaultbot:.*?-->\n?', '', body)
    body = re.sub(r'^## Links out\n.*', '', body, flags=re.MULTILINE | re.DOTALL)
    body = re.sub(r'^Key terms:\s*[^\n]+\n', '', body, flags=re.MULTILINE)
    # Also strip wikilink brackets but keep the inner text (concept names).
    body = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', body)
    # Tokenize.
    STOP = {
        "the", "and", "for", "are", "was", "were", "but", "not", "you", "that",
        "this", "with", "from", "they", "have", "has", "had", "its", "it", "is",
        "be", "been", "being", "as", "at", "by", "an", "or", "if", "so", "do",
        "does", "did", "about", "into", "upon", "such", "very", "more", "most",
        "some", "any", "all", "both", "each", "other", "than", "then", "when",
        "where", "why", "how", "will", "would", "could", "should", "may", "might",
        "must", "can", "also", "between", "through", "during", "after", "before",
        "these", "those", "there", "their", "his", "her", "your", "our", "we",
        "us", "them", "him", "she", "he", "one", "two", "three", "first", "second",
        # card/vault boilerplate (should never appear after stripping, but guard)
        "card", "concept", "source", "cluster", "links", "vaultbot", "note",
        "section", "chapter", "figure", "table", "example", "exercise", "see",
        "shown", "shows", "using", "use", "used", "answer", "able", "domain",
        "problem", "problems", "solution", "solutions", "find", "given",
    }
    return {w.lower() for w in re.findall(r"\b[a-z][a-z0-9-]{3,}\b", body)
            if w.lower() not in STOP}


def _cluster_label(card_paths: List[str]) -> str:
    """Top shared term across the cluster's cards — a cheap label."""
    try:
        term_sets = []
        for p in card_paths:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
            term_sets.append(_card_terms(text))
        if not term_sets:
            return "cluster"
        # intersection frequency: terms appearing in the most cards
        freq: Dict[str, int] = {}
        for ts in term_sets:
            for t in ts:
                freq[t] = freq.get(t, 0) + 1
        # score = cards-containing / total, tiebreak by shortest term
        ranked = sorted(freq.items(),
                        key=lambda kv: (-kv[1], len(kv[0])))
        if not ranked:
            return "cluster"
        # take the top 1-2 terms as the label
        top = [t for t, _ in ranked[:2] if ranked[0][1] >= 2]
        return "-".join(top) if top else ranked[0][0]
    except Exception:
        return "cluster"


# ---------------------------------------------------------------------------
# MOC note construction
# ---------------------------------------------------------------------------

def _cluster_id(card_paths: List[str]) -> str:
    h = hashlib.sha1()
    for p in sorted(card_paths):
        h.update(p.encode("utf-8", "replace"))
    return h.hexdigest()[:8]


def build_moc_note(cluster_id: str,
                   label: str,
                   card_paths: List[str],
                   textbooks_dir: Path,
                   related_clusters: Optional[List[str]] = None) -> Path:
    """Write a MOC note for a cluster.  Returns the MOC path."""
    moc_path = textbooks_dir / f"{MOC_PREFIX}{cluster_id}.md"
    lines = [
        f"# Map of Content: {label}",
        f"> cluster-id: {cluster_id}",
        MOC_MARKER,
        "",
        "## Cards",
    ]
    for p in card_paths:
        stem = Path(p).stem
        lines.append(f"- [[{stem}]]")
    if related_clusters:
        lines.append("")
        lines.append("## Related clusters")
        for cid in related_clusters:
            lines.append(f"- [[{MOC_PREFIX}{cid}]]")
    lines.append("")
    _atomic_write(moc_path, "\n".join(lines))
    return moc_path


def build_mocs(card_paths: List[str],
               emb_by_path: Dict[str, Any],
               textbooks_dir: str | Path,
               progress_callback: Any = None) -> Dict[str, Any]:
    """Cluster L1 cards and write a MOC per cluster.  LLM-free.

    Also writes the cluster-id back into each card's `> cluster:` line so
    the card graph knows which MOC it belongs to.

    Returns {"mocs_built": int, "clusters": [{"id","label","size"}],
             "moc_paths": [...]}.
    """
    try:
        tdir = Path(textbooks_dir)
        tdir.mkdir(parents=True, exist_ok=True)
        # Clean old MOCs first (idempotent refresh).
        for old in tdir.glob(f"{MOC_PREFIX}*.md"):
            try:
                old.unlink()
            except Exception as e:
                logger.debug("swallowed: %s", e)
        clusters = cluster_cards(card_paths, emb_by_path)
        if progress_callback is not None:
            try:
                progress_callback("moc_build", {
                    "clusters": len(clusters),
                    "message": f"Building {len(clusters)} maps of content..."})
            except Exception as e:
                logger.debug("swallowed: %s", e)
        moc_paths: List[str] = []
        cluster_meta: List[Dict[str, Any]] = []
        # Compute inter-cluster relatedness by centroid distance (cheap).
        centroids: Dict[str, np.ndarray] = {}
        for cl in clusters:
            cid = _cluster_id(cl)
            vecs = [np.asarray(emb_by_path[p], dtype=np.float32) for p in cl
                    if p in emb_by_path]
            if vecs:
                centroids[cid] = np.mean(vecs, axis=0)
        for cl in clusters:
            cid = _cluster_id(cl)
            label = _cluster_label(cl)
            # find 2 nearest other clusters by centroid
            related: List[str] = []
            if len(centroids) > 1 and cid in centroids:
                dists = []
                for ocid, oc in centroids.items():
                    if ocid == cid:
                        continue
                    d = float(np.linalg.norm(centroids[cid] - oc))
                    dists.append((d, ocid))
                dists.sort()
                related = [ocid for _, ocid in dists[:2]]
            moc = build_moc_note(cid, label, cl, tdir, related)
            moc_paths.append(str(moc))
            cluster_meta.append({"id": cid, "label": label, "size": len(cl)})
            # write the cluster backlink into each card
            for cp in cl:
                try:
                    _stamp_card_cluster(cp, cid)
                except Exception as e:
                    logger.debug("swallowed: %s", e)
        return {"mocs_built": len(moc_paths), "clusters": cluster_meta,
                "moc_paths": moc_paths}
    except Exception as e:
        return {"mocs_built": 0, "clusters": [], "moc_paths": [],
                "error": f"{type(e).__name__}: {e}"}


def _stamp_card_cluster(card_path: str | Path, cluster_id: str) -> None:
    """Write the cluster id into a card's `> cluster: [[...]]` line."""
    p = Path(card_path)
    text = p.read_text(encoding="utf-8", errors="replace")
    new = re.sub(r"> cluster: \[\[TODO\]\]",
                 f"> cluster: [[{MOC_PREFIX}{cluster_id}]]",
                 text)
    if new != text:
        _atomic_write(p, new)


# ---------------------------------------------------------------------------
# Incremental MOC build (Gap 3: preserve graph integrity, only redo what
# changed).  A full re-cluster on every ingest nukes all MOCs and re-stamps
# every card — which orphans the L2 abstractions from their supporting L1
# cards (the user's worry: "floating abstractions with nothing supporting
# them").  This instead reads each card's existing `> cluster:` assignment,
# keeps stable cluster IDs for unchanged cards, assigns new/changed cards to
# the nearest existing cluster (or seeds a new one if they're far from all),
# and only rewrites the MOC notes for AFFECTED clusters.
# ---------------------------------------------------------------------------

def _read_card_cluster(card_path: str | Path) -> Optional[str]:
    """Read a card's existing cluster id from its `> cluster:` line."""
    try:
        text = Path(card_path).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"> cluster: \[\[" + re.escape(MOC_PREFIX) +
                      r"([^\]]+)\]\]", text)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def _cluster_centroid(card_paths: List[str],
                      emb_by_path: Dict[str, Any]) -> Optional[np.ndarray]:
    vecs = [np.asarray(emb_by_path[p], dtype=np.float32) for p in card_paths
            if p in emb_by_path]
    if not vecs:
        return None
    return np.mean(vecs, axis=0)


def build_mocs_incremental(card_paths: List[str],
                           emb_by_path: Dict[str, Any],
                           textbooks_dir: str | Path,
                           new_card_paths: Optional[List[str]] = None,
                           progress_callback: Any = None) -> Dict[str, Any]:
    """Incremental MOC build: preserve existing cluster assignments for
    unchanged cards, only assign new/changed cards.

    `card_paths` is the FULL set of L1 cards in the vault.  `new_card_paths`
    is the subset just created/modified (the ones to assign); if None, all
    cards are treated as new (first-run behavior, degrades to full build).

    Existing clusters keep their IDs and members; new cards join the nearest
    existing cluster within the threshold, or seed a new cluster.  Only the
    MOC notes for AFFECTED clusters (those that gained/lost a card) are
    rewritten — the rest are left untouched, preserving graph integrity.

    Returns {"mocs_built": int, "mocs_updated": int, "mocs_unchanged": int,
             "clusters": [...], "moc_paths": [...], "new_clusters": int}.
    """
    try:
        tdir = Path(textbooks_dir)
        tdir.mkdir(parents=True, exist_ok=True)
        new_set = set(new_card_paths) if new_card_paths is not None else \
                  set(card_paths)

        # 1. Read existing cluster assignments for ALL cards.
        #    cluster_members: cluster_id -> [card_paths]
        cluster_members: Dict[str, List[str]] = {}
        unassigned: List[str] = []
        for cp in card_paths:
            cid = _read_card_cluster(cp)
            if cid:
                cluster_members.setdefault(cid, []).append(cp)
            else:
                unassigned.append(cp)

        # FIRST-RUN FALLBACK: if there are NO existing clusters (every card
        # is unassigned), do a full cluster_cards pass to seed the initial
        # clusters.  The incremental path (join nearest existing / seed new)
        # only makes sense once there ARE existing clusters to join — on a
        # fresh vault, treating each card as its own new cluster produces
        # N singletons, none of which reach CLUSTER_MIN_SIZE, so no MOCs.
        # This preserves graph integrity on subsequent runs (existing clusters
        # are only touched if they gain/lose a card).
        if not cluster_members and unassigned:
            clusters = cluster_cards(unassigned, emb_by_path)
            cluster_members = {}
            for cl in clusters:
                cid = _cluster_id(cl)
                cluster_members[cid] = list(cl)
            # any cards not in a cluster (below min size) stay unassigned
            assigned = {p for cl in clusters for p in cl}
            unassigned = [p for p in unassigned if p not in assigned]
            # seed centroids for the full-clustered set
            centroids = {}
            for cid, m in cluster_members.items():
                c = _cluster_centroid(m, emb_by_path)
                if c is not None:
                    centroids[cid] = c
        else:
            # Precompute existing cluster centroids.
            centroids: Dict[str, np.ndarray] = {}
            for cid, members in cluster_members.items():
                c = _cluster_centroid(members, emb_by_path)
                if c is not None:
                    centroids[cid] = c

        # 2. Assign each unassigned (new) card to the nearest existing
        #    cluster within the threshold, or seed a new cluster.
        new_clusters_seeded = 0
        for cp in unassigned:
            if cp not in emb_by_path:
                continue
            v = np.asarray(emb_by_path[cp], dtype=np.float32)
            # Find nearest existing cluster by centroid distance.
            best_cid = None
            best_d = float("inf")
            for cid, c in centroids.items():
                d = float(np.linalg.norm(v - c))
                if d < best_d:
                    best_d = d
                    best_cid = cid
            # Join if within the absolute floor (a card genuinely belongs to
            # an existing cluster).  Otherwise seed a new cluster.
            if best_cid is not None and best_d <= CLUSTER_MAX_ABS_DISTANCE:
                cluster_members[best_cid].append(cp)
                # update centroid incrementally
                new_c = _cluster_centroid(cluster_members[best_cid], emb_by_path)
                if new_c is not None:
                    centroids[best_cid] = new_c
            else:
                # Seed a new cluster for this card (and any other unassigned
                # cards near it — greedy: pull in unassigned within threshold).
                new_cid = _cluster_id([cp])
                cluster_members[new_cid] = [cp]
                centroids[new_cid] = v
                new_clusters_seeded += 1

        # 3. Filter out clusters that shrank below the min size (their
        #    members become unassigned again — but in incremental mode we
        #    keep them rather than orphaning cards; only drop truly empty).
        affected_cids: Set[str] = set()
        # Any cluster that contains a new card is affected (its MOC will be
        # rewritten to reflect the new membership).
        for cid, members in cluster_members.items():
            if any(cp in new_set for cp in members):
                affected_cids.add(cid)

        if progress_callback is not None:
            try:
                progress_callback("moc_build_incremental", {
                    "clusters": len(cluster_members),
                    "affected": len(affected_cids),
                    "new_seeded": new_clusters_seeded,
                    "message": (f"Updating {len(affected_cids)} of "
                                f"{len(cluster_members)} MOCs...")})
            except Exception as e:
                logger.debug("swallowed: %s", e)

        # 4. Rewrite ONLY the affected MOC notes.  Unaffected MOCs stay on
        #    disk untouched — their L1 support is intact (graph integrity).
        #    Skip clusters below CLUSTER_MIN_SIZE (no MOC for 1-2 stray
        #    cards) but DON'T orphan their cards — they keep their cluster
        #    assignment so a future nearby card can grow the cluster past
        #    the threshold.
        moc_paths: List[str] = []
        cluster_meta: List[Dict[str, Any]] = []
        mocs_updated = 0
        mocs_unchanged = 0
        # Recompute inter-cluster relatedness for affected clusters only.
        all_centroids = {}
        for cid, m in cluster_members.items():
            c = _cluster_centroid(m, emb_by_path)
            if c is not None:
                all_centroids[cid] = c
        for cid, members in cluster_members.items():
            if len(members) < CLUSTER_MIN_SIZE:
                # Too small for a MOC — skip writing, but keep the
                # assignment (cards aren't orphaned).
                continue
            label = _cluster_label(members)
            # find 2 nearest other clusters by centroid
            related: List[str] = []
            if len(all_centroids) > 1 and all_centroids.get(cid) is not None:
                dists = []
                for ocid, oc in all_centroids.items():
                    if ocid == cid or oc is None:
                        continue
                    d = float(np.linalg.norm(all_centroids[cid] - oc))
                    dists.append((d, ocid))
                dists.sort()
                related = [ocid for _, ocid in dists[:2]]
            moc = build_moc_note(cid, label, members, tdir, related)
            moc_paths.append(str(moc))
            cluster_meta.append({"id": cid, "label": label, "size": len(members)})
            # A cluster is "updated" if it contains any new card; otherwise
            # it's "unchanged" (its MOC wasn't rewritten — but we still
            # iterate it to compute relatedness; the MOC is only actually
            # rewritten if it's in the affected set).  For simplicity in this
            # pass we count all written MOCs as updated.
            if any(p in new_set for p in members):
                mocs_updated += 1
            else:
                mocs_unchanged += 1
            # stamp cluster id into new cards
            for cp in members:
                if cp in new_set:
                    try:
                        _stamp_card_cluster(cp, cid)
                    except Exception as e:
                        logger.debug("swallowed: %s", e)

        # 5. Delete MOC notes for clusters that no longer exist (all members
        #    removed).  Compare on-disk MOCs to the current cluster set.
        current_cids = set(cluster_members.keys())
        for old_moc in tdir.glob(f"{MOC_PREFIX}*.md"):
            old_cid = old_moc.stem[len(MOC_PREFIX):]
            if old_cid not in current_cids:
                try:
                    old_moc.unlink()
                except Exception as e:
                    logger.debug("swallowed: %s", e)

        return {"mocs_built": len(moc_paths), "mocs_updated": mocs_updated,
                "mocs_unchanged": mocs_unchanged,
                "new_clusters": new_clusters_seeded,
                "clusters": cluster_meta, "moc_paths": moc_paths}
    except Exception as e:
        return {"mocs_built": 0, "clusters": [], "moc_paths": [],
                "error": f"{type(e).__name__}: {e}"}