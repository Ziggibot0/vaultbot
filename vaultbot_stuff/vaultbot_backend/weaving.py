"""Post-ingest weaving: tie ingested textbook notes into the existing vault.

Extracted from main.py (lines ~2208-2820).  These functions run after the
textbook ingester writes section notes but before control returns to the
LLM, so the new content is linked into the existing graph (not inert text).

Two main directions:
  1. new -> old: outbound-link each new/updated section note — scan its body
     for plain-text mentions of EXISTING note titles and convert them to
     [[wikilinks]].  A-MEM doesn't do this direction.
  2. old -> new: A-MEM evolution on each section's NEIGHBORS (existing notes
     semantically similar to the new section) so they get backlinks +
     enriched tags.

Plus cross-book concept linking (LLM-free, FAISS-based) and the L1/L2
hierarchy build (concept cards + maps of content).

All singletons are accessed via a `Services` instance (services.py) instead
of reading main.py's module-level globals as free variables.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from concept_card import build_cards_batch
from fastapi import WebSocket
from moc_builder import build_mocs_incremental
from services import Services

_wlog = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-book concept linking (LLM-free, semantic)
# ---------------------------------------------------------------------------
# Prevents info islands between textbooks.  Two books covering the same
# concept (calculus + physics both covering "derivatives") won't share
# filenames (slugged differently per book), so the title-based outbound
# linker misses them.  This pass uses the FAISS index to find semantically
# similar sections ACROSS textbooks and inserts bidirectional
# "Related sections" wikilinks so the books are woven into one graph.
#
# LLM-free: reuses the embeddings computed during the index pass.  Idempotent:
# the "Related sections" block is rewritten cleanly each run (no duplicates).
# Tight threshold: only strong semantic matches get linked, so we don't
# spam a note with 20 weakly-related links.

_CROSS_LINK_HEADER = "## Related sections"
# Relative distance threshold for cross-linking.  We use a RELATIVE
# threshold (not absolute) because raw L2 distances in 768-dim
# nomic-embed-text space run 45-195, not 0-1.  A cross-book candidate
# is linked if its distance is within _CROSS_LINK_DISTANCE_RATIO × the
# nearest cross-book candidate's distance.  With nearest=45 and ratio=2.0,
# that's ≤90 — catches genuine concept overlap (thermo sections at 45-60)
# while excluding unrelated notes (kinematics at 195).  Adapts to any
# embedding model's distance scale.
_CROSS_LINK_DISTANCE_RATIO = 2.0
_CROSS_LINK_MAX_PER_NOTE = 5  # cap links per note to avoid link spam
# Absolute floor: never link if the nearest cross-book candidate is farther
# than this.  Prevents linking in a vault where everything is roughly
# equidistant (no real semantic structure).  300 is well above the
# thermo-kinematics gap (195) so genuine matches always pass; a vault with
# only loosely-related notes won't get spam.
_CROSS_LINK_MAX_ABS_DISTANCE = 300.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send_progress(svc: Services, websocket: WebSocket | None,
                          stage: str,
                          detail: dict[str, Any] | None = None) -> None:
    """Send a structured progress event to the live UI.

    Extracted mirror of main.py's `_send_progress`; uses the Services
    registry's `manager` + `session_logger` instead of the module globals.
    """
    try:
        await svc.manager.send_personal_message(
            json.dumps({"type": "progress", "stage": stage,
                         "detail": detail or {}}),
            websocket, session_logger=svc.session_logger)
    except Exception as e:
        _wlog.debug("weaving progress send failed: %s", e)


# ---------------------------------------------------------------------------
# Public API (extracted from main.py; underscore dropped)
# ---------------------------------------------------------------------------

def existing_note_titles(svc: Services) -> dict:
    """Return {normalized_title: file_path} for every note in the vault.

    Used to detect plain-text mentions worth wikilinking. Normalized = the
    Obsidian wikilink form (lowercased). Excludes the ingester's own textbook
    notes so we don't link textbook-to-textbook (that's the ingester's job).

    Sourced from the in-memory vault graph (``svc.vault_graph.nodes``) instead
    of a full ``rglob`` over the vault — the graph already holds every note's
    stem + file_path in memory, so this is a dict walk instead of a disk
    scan. The graph's ignore-dir filter (venv/.obsidian/.git/etc.) already
    applies; the textbooks-folder exclusion is applied here.
    """
    titles: dict[str, str] = {}
    try:
        for _name, node in (svc.vault_graph.nodes or {}).items():
            fp = node.get("file_path") or ""
            if not fp:
                continue
            # Skip the textbooks/ folder — those are what we're weaving.
            if ("vaultbot_stuff/Knowledge/Textbooks" + os.sep
                    not in fp + os.sep):
                pass  # not a textbook note — keep it
            else:
                continue
            stem = Path(fp).stem
            if len(stem) < 3:
                continue  # too short to link safely (e.g. "a", "is")
            titles[stem.lower()] = fp
    except Exception as e:
        _wlog.warning("existing_note_titles failed: %s", e)
    return titles


def is_ignored_index_path(p: Path) -> bool:
    """True for vault subpaths the indexer/graph ignore (venv, index, etc.)."""
    parts = str(p).replace("\\", "/").lower()
    ignored = ("vaultbot_venv/", "vaultbot_stuff/vaultbot_backend/vaultbot_index/",
               "vaultbot_stuff/vaultbot_backend/partials/", ".git/")
    return any(seg in parts for seg in ignored)


def link_outbound(note_path: str, title_map: dict) -> int:
    """Convert plain-text mentions of existing note titles in a note into
    [[wikilinks]]. Returns the number of links inserted.

    Safe rules (won't corrupt the note):
      - Only links titles >= 4 chars (avoids linking "the", "a", "is").
      - Only links titles that appear as whole words, case-insensitively.
      - Never wraps a mention that's already inside [[...]] or is a URL.
      - Skips the title line (H1) so the note's own heading isn't self-linked.
      - Atomic write; never raises (returns 0 on any failure).
    """
    try:
        p = Path(note_path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text:
            return 0
        lines = text.split("\n")
        # Skip the first H1 line (the note's own heading).
        start = 1 if lines and lines[0].lstrip().startswith("# ") else 0
        links_added = 0
        for stem_lower, _fp in title_map.items():
            if len(stem_lower) < 4:
                continue
            # Match the title as a whole word, case-insensitive, but NOT when
            # it's already inside a wikilink. The lookbehind/lookahead block
            # matches right after `[[` or right before `]]`, so an already-
            # linked mention is skipped. A bare mention mid-sentence matches.
            pattern = re.compile(
                r"(?<!\[\[)\b" + re.escape(stem_lower) + r"\b(?!\]\])",
                re.IGNORECASE)
            for i in range(start, len(lines)):
                # Don't link a mention that sits inside a URL.
                if "http" in lines[i] and stem_lower in lines[i].lower():
                    # Could still link a non-URL word on the same line; the
                    # subn count=1 picks the first match, so only skip if the
                    # first match is inside the URL. Simplest safe rule: skip
                    # the line entirely if the title appears inside an http link.
                    url_match = re.search(r"https?://\S*", lines[i])
                    if url_match and stem_lower in url_match.group(0).lower():
                        continue
                new_line, n = pattern.subn(
                    lambda m: f"[[{m.group(0)}]]", lines[i], count=1)
                if n:
                    lines[i] = new_line
                    links_added += n
        if links_added:
            p.write_text("\n".join(lines), encoding="utf-8")
        return links_added
    except Exception:
        return 0


def index_note_now(svc: Services, note_path: str) -> None:
    """Index a single note immediately so it's searchable right away (instead
    of waiting for the background watcher). Failure-isolated.
    """
    try:
        svc.vault_indexer._add_file(note_path)
        svc.vault_indexer.persist()
    except Exception as e:
        _wlog.warning("index_add for %s failed: %s", note_path, e)


def cross_link_textbooks(svc: Services,
                          new_abs_paths: list[str],
                          emb_by_path: dict,
                          source_keys: set | None = None) -> dict:
    """Cross-link the newly-ingested textbook sections to OTHER textbook
    sections in the vault that are semantically similar.

    For each new note, finds the top-k closest OTHER textbook notes via the
    FAISS index (excluding itself + notes from the same book), and inserts a
    "## Related sections" block with [[wikilinks]] to the strong matches.
    The link is bidirectional: the other note also gets a backlink to the
    new note.

    Args:
      svc: Services registry (uses ``svc.vault_indexer``).
      new_abs_paths: absolute paths of the notes just ingested.
      emb_by_path: {abs_path: embedding_list} from the index pass (reused
        so we don't re-embed).
      source_keys: optional set of abs paths belonging to the SAME book as
        the new notes — these are excluded as cross-link targets (a book
        shouldn't cross-link to its own sections; the ingester handles
        intra-book nav).  If None, same-book exclusion is skipped.

    Returns {"cross_links_added": int, "notes_linked": int}; never raises.
    """
    out: dict[str, Any] = {"cross_links_added": 0, "notes_linked": 0}
    try:
        textbooks_dir = (Path(os.getenv("VAULT_PATH", "."))
                         / "vaultbot_stuff/Knowledge/Textbooks")
        if not textbooks_dir.exists():
            return out
        # Build the set of all textbook note paths (candidates for cross-linking).
        all_textbook_paths = [str(p) for p in textbooks_dir.rglob("*.md")]
        if len(all_textbook_paths) < 2:
            return out
        import numpy as _np
        for new_path in new_abs_paths:
            try:
                emb = emb_by_path.get(new_path)
                if emb is None:
                    continue
                # Find nearest neighbors among ALL indexed notes.
                hits = svc.vault_indexer.search_by_vector(
                    _np.asarray(emb, dtype=_np.float32),
                    k=15)  # over-fetch then filter
                # Filter to: textbook notes, not self, not same book.
                # We collect ALL cross-book textbook candidates first, then
                # apply the relative distance threshold (link to candidates
                # within _CROSS_LINK_DISTANCE_RATIO × the nearest candidate's
                # distance).  This adapts to any embedding model's distance
                # scale — raw L2 in 768-dim space runs 45-195, not 0-1.
                candidates: list = []
                for h in hits:
                    fp = h.get("file_path", "")
                    if not fp or fp == new_path:
                        continue
                    fp_norm = str(Path(fp).resolve())
                    new_norm = str(Path(new_path).resolve())
                    if fp_norm == new_norm:
                        continue
                    # Must be a textbook note.
                    if "vaultbot_stuff/Knowledge/Textbooks" + os.sep not in fp_norm + os.sep:
                        continue
                    # Exclude same-book notes if we have source_keys.
                    if source_keys and fp_norm in source_keys:
                        continue
                    dist = h.get("score", 999.0)
                    candidates.append((fp, dist))
                if not candidates:
                    continue
                # Sort by distance (closest first).
                candidates.sort(key=lambda x: x[1])
                nearest = candidates[0][1]
                # Absolute floor: if even the nearest cross-book candidate is
                # very far away, there's no real semantic match — skip.
                if nearest > _CROSS_LINK_MAX_ABS_DISTANCE:
                    continue
                # Relative threshold: keep candidates within
                # _CROSS_LINK_DISTANCE_RATIO × nearest.
                cutoff = nearest * _CROSS_LINK_DISTANCE_RATIO
                links = [(fp, d) for fp, d in candidates if d <= cutoff]
                links = links[:_CROSS_LINK_MAX_PER_NOTE]
                if not links:
                    continue
                # Insert/refresh the "Related sections" block in the new note
                # + a backlink in each target note.
                added = insert_related_block(new_path, links)
                if added:
                    out["cross_links_added"] += added
                    out["notes_linked"] += 1
            except Exception:
                continue
    except Exception as e:
        _wlog.warning("outbound linking failed: %s", e)
    return out


def insert_related_block(note_path: str,
                           links: list) -> int:
    """Insert (or refresh) a "## Related sections" block in a note with
    wikilinks to the given target paths, and insert a backlink block in
    each target.  Returns the number of links inserted; never raises.

    Idempotent: if the block already exists, it's rewritten cleanly (no
    duplicates).  The block is placed before the `---\n**Navigation:**`
    footer so it sits with the body, not in the nav.
    """
    try:
        p = Path(note_path)
        text = p.read_text(encoding="utf-8", errors="replace")
        # Build the new Related sections block.
        lines = []
        for fp, _dist in links:
            stem = Path(fp).stem
            lines.append(f"- [[{stem}]]")
        block = _CROSS_LINK_HEADER + "\n" + "\n".join(lines) + "\n"
        # Remove any existing Related sections block (idempotent refresh).
        text = strip_related_block(text)
        # Insert before the navigation footer.
        nav_idx = text.find("\n---\n**Navigation:**")
        if nav_idx == -1:
            nav_idx = text.find("\n---\n")
        if nav_idx == -1:
            text = text.rstrip() + "\n\n" + block
        else:
            text = text[:nav_idx].rstrip() + "\n\n" + block + text[nav_idx:]
        p.write_text(text, encoding="utf-8")
        # Backlinks: add the new note to each target's Related sections block.
        new_stem = p.stem
        added = 0
        for fp, _dist in links:
            try:
                tp = Path(fp)
                ttext = tp.read_text(encoding="utf-8", errors="replace")
                ttext = strip_related_block(ttext)
                back_block = (_CROSS_LINK_HEADER + "\n"
                              + f"- [[{new_stem}]]\n")
                tnav_idx = ttext.find("\n---\n**Navigation:**")
                if tnav_idx == -1:
                    tnav_idx = ttext.find("\n---\n")
                if tnav_idx == -1:
                    ttext = ttext.rstrip() + "\n\n" + back_block
                else:
                    ttext = (ttext[:tnav_idx].rstrip() + "\n\n"
                             + back_block + ttext[tnav_idx:])
                tp.write_text(ttext, encoding="utf-8")
                added += 1
            except Exception:
                continue
        return added + len(links)
    except Exception:
        return 0


def strip_related_block(text: str) -> str:
    """Remove an existing '## Related sections' block from a note (idempotent)."""
    # Match the header + its bullet lines up to the next blank line / heading / ---.
    pat = re.compile(
        r"\n?## Related sections\n(?:- \[\[[^\]]+\]\]\n)+\n?",
        re.MULTILINE)
    return pat.sub("\n", text)


async def weave_textbook_notes(svc: Services,
                                ingest_result: dict,
                                websocket: WebSocket | None = None,
                                session_logger: Any | None = None) -> dict:
    """Run the two post-ingest passes over every section note the ingester
    created or updated. Returns a summary; never raises.

    If `websocket` is provided, sends live progress events so the user sees
    "linking 47/129…" instead of a frozen screen during a long weave.
    """
    out: dict[str, Any] = {
        "indexed": 0, "outbound_links_added": 0,
        "amem_evolved": 0, "amem_links_added": 0,
        "cross_links_added": 0, "notes_cross_linked": 0,
        "notes": [],
        "status": "complete",
    }
    sl = session_logger or svc.session_logger
    try:
        created = ingest_result.get("notes_created", [])
        updated = ingest_result.get("notes_updated", [])
        note_rels = created + updated
        total = len(note_rels)
        if not note_rels:
            return out
        # Resolve absolute paths. The ingester returns paths relative to its
        # VAULT_DIR (which is <vault_root>), so paths are relative to vault root
        # when joining to the vault root. We try both forms to be safe.
        vault_root = Path(os.getenv("VAULT_PATH", "."))
        title_map = existing_note_titles(svc)
        loop = asyncio.get_event_loop()

        if websocket is not None:
            await _send_progress(svc, websocket, "weaving_begin", {
                "total_notes": total,
                "message": f"Linking {total} textbook notes into the vault..."})

        # Resolve all absolute paths first
        abs_paths: list[str] = []
        for rel in note_rels:
            candidate = (vault_root / rel).resolve()
            if not candidate.exists():
                candidate = (vault_root / rel).resolve()
            abs_paths.append(str(candidate))

        # --- Pass 1: batch-index all notes in parallel --- #
        # This is the slow part (embedding calls).  We fire them all at once
        # via ThreadPoolExecutor so Ollama processes them concurrently, and
        # we ASK FOR THE EMBEDDINGS BACK so the A-MEM pass below can reuse
        # them as neighbor-search queries instead of re-embedding each note
        # (saves one embedding call per note — ~129 calls on a big ingest).
        if websocket is not None:
            await _send_progress(svc, websocket, "weaving_progress", {
                "note": 0, "total": total,
                "message": f"Indexing {total} notes in parallel..."})
        indexed, emb_by_path = await loop.run_in_executor(
            None, svc.vault_indexer.batch_add_files, abs_paths, True)
        out["indexed"] = indexed

        # One graph refresh for the whole weave — the graph doesn't change
        # between consecutive notes in the same ingest, so refreshing once
        # here (instead of inside every evolve_on_create) saves N full vault
        # rescans.  A-MEM is told to skip its own refresh via skip_refresh.
        try:
            svc.vault_graph.refresh()
        except Exception as e:
            _wlog.warning("vault_graph.refresh failed: %s", e)

        # --- Pass 2: outbound links + A-MEM (sequential, fast) --- #
        # A-MEM runs in heuristic_only mode here: the per-neighbor LLM
        # tag-suggestion call is skipped entirely, so a 129-note ingest
        # makes ZERO generative LLM calls during the weave (the single-note
        # vault_research path still uses the LLM).  The heuristic adds the
        # new note's title as a tag + inserts a backlink — most of A-MEM's
        # value for textbook sections, which have unambiguous titles.
        for idx, (rel, abs_path) in enumerate(zip(note_rels, abs_paths)):
            if websocket is not None and (idx % 10 == 0 or idx == total - 1):
                await _send_progress(svc, websocket, "weaving_progress", {
                    "note": idx + 1, "total": total,
                    "message": f"Linking note {idx+1}/{total}..."})

            # outbound-link into existing notes
            added = await loop.run_in_executor(
                None, link_outbound, abs_path, title_map)
            out["outbound_links_added"] += added
            # A-MEM: evolve existing neighbors (old -> new backlinks).
            # heuristic_only=True skips the LLM; query_embedding reuses the
            # embedding we just computed during indexing; skip_refresh=True
            # because we refreshed the graph once above.
            try:
                content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            ev = await loop.run_in_executor(
                None, lambda c=content, a=abs_path: svc.amem.evolve_on_create(
                    a, c,
                    heuristic_only=True,
                    query_embedding=emb_by_path.get(a),
                    skip_refresh=True))
            if ev.get("evolved_count"):
                out["amem_evolved"] += ev["evolved_count"]
            out["amem_links_added"] += ev.get("links_added", 0)
            out["notes"].append({
                "note": rel, "outbound": added,
                "neighbors_evolved": ev.get("evolved_count", 0),
            })

        # --- Pass 3: cross-book concept linking (LLM-free, semantic) --- #
        # The outbound linker (pass 2) explicitly excludes textbooks, so two
        # books covering the same concept (calculus + physics both covering
        # "derivatives") stay invisible to each other — info islands.  This
        # pass uses the FAISS index + the embeddings we already computed to
        # find semantically similar sections ACROSS textbooks and insert
        # bidirectional "## Related sections" wikilinks.  Tight distance
        # threshold (0.75) so only genuine concept overlap gets linked, not
        # "both are about math."  Idempotent.  Same-book notes excluded.
        if websocket is not None:
            await _send_progress(svc, websocket, "weaving_progress", {
                "note": total, "total": total,
                "message": f"Cross-linking {total} notes to other textbooks..."})
        # Build the set of paths belonging to THIS ingest's book so we don't
        # cross-link a book to its own sections (intra-book nav is the
        # ingester's job).
        source_keys = set(abs_paths)
        cross = await loop.run_in_executor(
            None, cross_link_textbooks, svc, abs_paths, emb_by_path, source_keys)
        out["cross_links_added"] = cross.get("cross_links_added", 0)
        out["notes_cross_linked"] = cross.get("notes_linked", 0)

        # --- Pass 4: L1 concept cards (LLM-free abstraction layer) --- #
        # Build a terse concept card (~300-500 chars) for each L0 section so
        # the chat loop can walk the ABSTRACT graph (cards) instead of the
        # raw graph (full chapters).  Cards point back to their L0 source
        # via `> source: [[...]]`.  Zero LLM calls — extractive sketch only.
        # Cards are first-class vault nodes: indexed by FAISS, walked by the
        # link graph, hop-able by the LLM at ~1/100th the context cost of L0.
        if websocket is not None:
            await _send_progress(svc, websocket, "weaving_progress", {
                "note": total, "total": total,
                "message": f"Building concept cards for {total} notes..."})
        try:
            card_result = await loop.run_in_executor(
                None, build_cards_batch, abs_paths, svc.vault_graph, None)
            out["cards_built"] = card_result.get("cards_built", 0)
            card_paths = card_result.get("card_paths", [])
        except Exception as e:
            out["cards_built"] = 0
            card_paths = []
            try:
                sl.log("concept_card_build_failed", {"error": str(e)})
            except Exception:
                _wlog.debug("concept_card_build_failed log failed: %s", e)

        # --- Pass 5: L2 maps of content (incremental, graph-integrity-
        # preserving).  Cluster the L1 cards by embedding similarity and
        # write/update one MOC note per cluster.  INCREMENTAL: existing
        # clusters keep their IDs + members (so L2 abstractions stay
        # supported by their L1 cards — no "floating abstractions"); only
        # new/changed cards are assigned (to the nearest existing cluster
        # within threshold, or seed a new one), and only AFFECTED MOC notes
        # are rewritten.  Reuses the ingest embeddings — zero new embedding
        # calls for clustering; only the new cards needed indexing. --- #
        if card_paths:
            if websocket is not None:
                await _send_progress(svc, websocket, "weaving_progress", {
                    "note": total, "total": total,
                    "message": f"Clustering {len(card_paths)} new cards into maps of content..."})
            # Index the new cards so they're in the FAISS index + get their
            # embeddings back for clustering.  This is the only new embedding
            # cost of the whole hierarchy build, and it's parallel + local.
            try:
                _cn, card_embs = await loop.run_in_executor(
                    None, svc.vault_indexer.batch_add_files, card_paths, True)
                svc.vault_graph.refresh()
                textbooks_dir = (Path(os.getenv("VAULT_PATH", ".")) / "vaultbot_stuff/Knowledge/Textbooks")
                # Gather ALL L1 cards in the vault (incremental mode needs
                # the full set to preserve existing cluster assignments;
                # only the new subset gets assigned).  Merge the new
                # embeddings with any existing ones we can recover.
                all_card_paths = [str(p) for p in textbooks_dir.rglob("*-L1.md")]
                # The new cards' embeddings are in card_embs; for existing
                # cards not in this batch, recover their embeddings from the
                # FAISS index via search_by_vector on themselves (cheap —
                # we have the content).  Fall back to re-embedding only if
                # needed.
                full_embs = dict(card_embs)
                missing = [p for p in all_card_paths if p not in full_embs]
                if missing:
                    try:
                        _mn, recovered = await loop.run_in_executor(
                            None, svc.vault_indexer.batch_add_files, missing, True)
                        full_embs.update(recovered)
                    except Exception as e:
                        _wlog.warning("batch_add missing cards failed: %s", e)
                moc_result = await loop.run_in_executor(
                    None, build_mocs_incremental, all_card_paths, full_embs,
                    str(textbooks_dir), card_paths, None)
                out["mocs_built"] = moc_result.get("mocs_built", 0)
                out["mocs_updated"] = moc_result.get("mocs_updated", 0)
                out["mocs_unchanged"] = moc_result.get("mocs_unchanged", 0)
                out["new_clusters"] = moc_result.get("new_clusters", 0)
                out["clusters"] = moc_result.get("clusters", [])
                # Re-index the MOC notes that were written/updated.
                moc_paths = moc_result.get("moc_paths", [])
                if moc_paths:
                    await loop.run_in_executor(
                        None, svc.vault_indexer.batch_add_files, moc_paths, False)
                try:
                    sl.log("hierarchy_built", {
                        "cards": out.get("cards_built", 0),
                        "mocs": out.get("mocs_built", 0),
                        "mocs_updated": out.get("mocs_updated", 0),
                        "mocs_unchanged": out.get("mocs_unchanged", 0),
                        "new_clusters": out.get("new_clusters", 0),
                        "clusters": len(out.get("clusters", []))})
                except Exception:
                    _wlog.debug("hierarchy_built log failed")
            except Exception as e:
                out["mocs_built"] = 0
                try:
                    sl.log("moc_build_failed", {"error": str(e)})
                except Exception:
                    _wlog.debug("moc_build_failed log failed: %s", e)
        else:
            out["mocs_built"] = 0

        if websocket is not None:
            await _send_progress(svc, websocket, "weaving_done", {
                "total_notes": total,
                "indexed": out["indexed"],
                "outbound_links": out["outbound_links_added"],
                "amem_evolved": out["amem_evolved"],
                "amem_links": out["amem_links_added"],
                "cross_links": out.get("cross_links_added", 0),
                "notes_cross_linked": out.get("notes_cross_linked", 0),
                "cards_built": out.get("cards_built", 0),
                "mocs_built": out.get("mocs_built", 0),
                "message": (f"Done: {out['outbound_links_added']} outbound links, "
                            f"{out['amem_evolved']} neighbors evolved, "
                            f"{out.get('cross_links_added', 0)} cross-book links, "
                            f"{out.get('cards_built', 0)} concept cards, "
                            f"{out.get('mocs_built', 0)} maps of content "
                            f"across {total} notes.")})

        sl.log("textbook_weave_complete", {
            "total": total, "indexed": out["indexed"],
            "outbound_links": out["outbound_links_added"],
            "amem_evolved": out["amem_evolved"],
            "amem_links": out["amem_links_added"],
            "cross_links": out.get("cross_links_added", 0),
            "notes_cross_linked": out.get("notes_cross_linked", 0),
            "cards_built": out.get("cards_built", 0),
            "mocs_built": out.get("mocs_built", 0)})
    except Exception as e:
        out["error"] = str(e)
        out["status"] = "error"
        sl.log("textbook_weave_failed", {"error": str(e)})
        if websocket is not None:
            await _send_progress(svc, websocket, "weaving_done", {
                "message": f"Weaving completed with errors: {str(e)[:100]}"})
    return out
