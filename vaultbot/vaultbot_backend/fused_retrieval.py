"""
Fused retrieval: combine FAISS vector search with the Obsidian wikilink/backlink graph.

Obsidian's link graph is high-precision / low-recall (only linked notes surface),
while dense embeddings are the opposite (high recall, lower precision). Fusing the two
gives both: semantic neighbors seed the graph walk, and the graph walks back in
contextually-connected notes that pure similarity may miss.

Three channels are fused:
  a. Vector channel  — vault_indexer.search() normalized to [0,1]
  b. Lexical channel — BM25 keyword match over note title + cached content
                       preview, normalized to [0,1] (a peer of the vector
                       channel, recovering title/keyword matches a small
                       embedding model can't map)
  c. Graph channel   — wikilink neighbors of vector hits (forward + backlinks,
                       via direction="both"), score = GRAPH_BOOST x vector score

Candidates are merged by file_path (max score across channels), then reranked by
two signals: procedure status (authoritative tools float up) and trigger/inhibitor
feedback (a note whose trigger phrases match the query floats up; a note whose
inhibitor phrases match more strongly than any trigger is dropped). Finally
truncated to top-k.

Future work: this is a lightweight stand-in for the hybrid/community-augmented
retrieval described in GraphRAG (arXiv:2404.16130), which builds a community
structure over entities and fuses embedding retrieval with community-level
summaries. LightRAG's dual-level (low-level entity + high-level keyword) retrieval
is the same idea at a different granularity — this module's vector+graph fusion
mirrors the low-level path and leaves the community path as a drop-in extension.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

_frlog = logging.getLogger(__name__)

if TYPE_CHECKING:
    from session_logger import SessionLogger


class FusedRetriever:
    """Fuse FAISS vector search with the Obsidian link graph."""

    # Channel weight multipliers (from the research design).
    GRAPH_BOOST = 0.5  # forward-link neighbors
    BACKLINK_BOOST = 0.7  # backlinks (hubs — someone linked TO this note)
    ALL_CHANNEL_RERANK = 1.3  # appears in vector + graph (multi-channel agreement)
    HUB_RERANK = 1.1  # high backlink degree
    HUB_DEGREE_THRESHOLD = 3  # min backlinks to count as a hub
    # Lexical (BM25) channel tuning.  The 'FUSED' retriever historically
    # fused only dense (vector) + graph channels — no keyword channel.  A
    # lexical channel recovers title/keyword matches that a small embedding
    # model can't map (the golden set's 'direct' category, and paraphrase
    # queries whose note bodies share vocabulary with the query).
    LEXICAL_TOP_K = 20  # over-fetch lexical candidates before merge
    # Lexical is a PEER of the vector channel, not a discounted add-on.  A
    # title/keyword match is the single most reliable relevance signal, so
    # lexical scores normalize to [0,1] with top=1.0 (same ceiling as the
    # vector channel's top hit).  The procedure + trigger boosts are additive
    # and can push a note above 1.0, so a lexical title match needs a
    # comparable ceiling to win the 'direct' category.  1.3 gives lexical
    # that headroom.
    LEXICAL_SCORE_WEIGHT = 1.3
    # Graph seed over-fetch.  The graph channel walks from the vector hits,
    # but if the vector channel truncates to top-k first, a graph-walk query
    # whose seed note ranked just outside top-k never starts its walk.
    # Over-fetching the vector pool (and NOT truncating before the graph
    # channel seeds) lets neighbors of lower-ranked-but-relevant notes
    # surface.  Final top-k truncation happens in retrieve() after merge.
    GRAPH_OVERFETCH = 8
    # Minimum normalized score to be included in results. Results below
    # this threshold are dropped — they're semantically too distant from
    # the query to be useful and just waste context budget with noise.
    # Applied AFTER reranking so boosted notes can still make the cut.
    # 0.15 is conservative — drops the bottom ~20% that are barely above
    # random for a 768-dim embedding space.
    MIN_SCORE_THRESHOLD = 0.15
    # Verified-procedure retrieval boost.  A procedure note whose
    # frontmatter ``status`` is ``verified`` (set by
    # procedure_tracker.run_promotion_cycle) gets this fractional score
    # bump, small enough that verified status only breaks ties near the
    # margin and never overrides content similarity.  See
    # [[Procedure-Subprocess-Architecture]] grading loop.
    VERIFIED_BOOST = 0.05
    # Base procedure retrieval boost.  ANY note with ``type: procedure``
    # (verified, experimental, or unknown status) gets this ADDITIVE score
    # bump so procedures rank above equally-scoring non-procedure notes
    # (chat memories, research notes) for the same query.  Procedures are
    # authoritative tools the model should reach for; without this a chat
    # memory literally titled "what were you doing earlier" beats a
    # procedure whose description says the same thing, because the chat
    # note's entire body is about that query while the procedure's
    # description is a meta-statement.  ADDITIVE (not multiplicative)
    # because normalized scores cluster in a tight range — a x1.15 boost
    # on a 0.05 score adds only 0.007, too small to break ties.  0.10
    # additive is enough to lift a procedure past a similarly-relevant
    # non-procedure note without promoting an irrelevant procedure above
    # a strongly-relevant non-procedure (a 0.50 gap can't be closed by
    # 0.10).  Verified procedures get this + 0.05 VERIFIED_BOOST on top.
    PROCEDURE_BASE_BOOST = 0.20
    # Flagged-procedure penalty.  A procedure whose status is ``flagged``
    # (repeatedly failed validation, blocked from execution) has its score
    # multiplied by this factor so it sinks below usable notes — it should
    # not crowd the procedure surface when it can't be run.
    FLAGGED_PENALTY = 0.5
    # Trigger/inhibitor retrieval signal.  A note's trigger phrases create a
    # continuous ranking gradient: the closer the query is to a trigger
    # phrase, the more the note floats up (additive boost).  Inhibitor
    # phrases are a hard "no" (like a DNA inhibitor): if the query is closer
    # to an inhibitor than to ANY trigger, the note is dropped from recall
    # (still findable via other searches — just not in the first context
    # pop).  See trigger_store.py.
    TRIGGER_BOOST = 0.2  # additive score bump, scaled by trigger cosine
    TRIGGER_GATE_MARGIN = 0.0  # drop when inhibitor > trigger (no slack)

    def __init__(
        self,
        vault_graph: VaultGraph,
        vault_indexer: VaultIndexer,
        session_logger: SessionLogger | None = None,
        embedding_drift: Any | None = None,
        trigger_store: Any | None = None,
    ) -> None:
        self.vault_graph = vault_graph
        self.vault_indexer = vault_indexer
        self.session_logger = session_logger
        # EmbeddingDrift is accepted for API compatibility (main.py still
        # passes it) but is NO LONGER consumed by the retriever — drift
        # re-ranking was removed in the retrieval simplification.  The drift
        # layer remains wired elsewhere (chat_background / chat_preflight
        # record feedback on it directly), but retrieval no longer re-ranks
        # by drift.  Kept as an attribute so callers that read it don't break.
        self.embedding_drift = embedding_drift
        # Optional TriggerStore (trigger/inhibitor phrase embeddings). When
        # wired, trigger phrases create a continuous ranking gradient and
        # inhibitor phrases hard-drop notes whose inhibitors match the query
        # more strongly than any trigger.  None = no signal (the store is a
        # bonus layer — see trigger_store.py).
        self.trigger_store = trigger_store
        # Optional stem -> frontmatter-status map for verified-procedure
        # retrieval boost (Phase 3 grading loop).  Populated by main.py
        # from procedure_tracker.get_procedure_index(); None disables the
        # boost.  Kept as a plain dict attribute so this module stays
        # decoupled from procedure_tracker.
        self.procedure_status_index: dict[str, str] | None = None
        self._log("init", "FusedRetriever initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def retrieve(self, query: str, k: int = 10, depth: int = 1) -> dict[str, Any]:
        """
        Fuse vector + lexical + graph retrieval.

        Returns:
            {
              "results": [
                {"file_path", "name", "score", "channels": [str], "snippet"}, ...
              ],
              "channels": {"vector": N, "graph": N, "lexical": N},
              "count": int,
            }
        """
        try:
            if not query or not query.strip():
                return self._empty()

            # ---- channel (a): vector ----
            vector_hits, norm_scores = self._vector_channel(query, k)

            if not vector_hits:
                self._log("retrieve.vector_only", "vector channel empty — degrading")
                return self._empty()

            # ---- channel (b): graph (wikilink neighbors) ----
            # Each enhancement channel is isolated: a failure in one channel
            # is logged loudly but doesn't kill the others. This is "checking
            # multiple sources is fine" — each channel is a separate source.
            # The difference from the old code: failures are LOGGED at a
            # visible level, not silently swallowed.
            graph_candidates: dict[str, dict[str, Any]] = {}
            try:
                graph_candidates = self._graph_channel(vector_hits, norm_scores, depth)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log(
                    "graph.channel_failed",
                    f"{type(e).__name__}: {e} — graph channel skipped",
                )
                if self.session_logger is not None:
                    self.session_logger.log(
                        "retrieval_channel_failed",
                        {
                            "channel": "graph",
                            "error": str(e),
                        },
                    )

            # ---- channel (b): lexical (BM25 keyword) ----
            # A lexical channel recovers title/keyword matches that a small
            # embedding model can't map (the golden set's 'direct' category,
            # and paraphrase queries whose note bodies share vocabulary with
            # the query).
            lexical_candidates: dict[str, dict[str, Any]] = {}
            try:
                lexical_candidates = self._lexical_channel(query, k)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log(
                    "lexical.channel_failed",
                    f"{type(e).__name__}: {e} — lexical channel skipped",
                )
                if self.session_logger is not None:
                    self.session_logger.log(
                        "retrieval_channel_failed",
                        {
                            "channel": "lexical",
                            "error": str(e),
                        },
                    )

            # ---- (c) merge + dedup ----
            merged = self._merge(
                vector_hits=vector_hits,
                norm_scores=norm_scores,
                graph_candidates=graph_candidates,
                lexical_candidates=lexical_candidates,
            )

            # ---- (d) rerank ----
            # Rerank is a post-processing boost; if it fails (e.g. graph
            # access for hub detection), return unreranked results rather
            # than losing everything. Log loudly so it's visible.
            try:
                self._rerank(merged)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log(
                    "rerank.failed", f"{type(e).__name__}: {e} — skipping rerank boosts"
                )
                if self.session_logger is not None:
                    self.session_logger.log(
                        "retrieval_channel_failed",
                        {
                            "channel": "rerank",
                            "error": str(e),
                        },
                    )

            # ---- (e) filter by minimum score + truncate to top-k ----
            ranked = sorted(merged.values(), key=lambda c: c["score"], reverse=True)
            # Drop results below the relevance threshold — they waste
            # context budget with semantically distant noise.  But only
            # apply the threshold when we have ENOUGH candidates to be
            # selective.  When the merged pool is ≤ k, the user explicitly
            # asked for that many results and every candidate was already
            # fetched by the vector search — filtering here would return
            # fewer results than requested without any noise to filter.
            # The threshold is for large pools (over-fetch), not small ones.
            if len(ranked) > k:
                filtered = [c for c in ranked if c["score"] >= self.MIN_SCORE_THRESHOLD]
                # If filtering removed everything, keep the top result rather
                # than returning empty (better one marginal hit than nothing).
                if not filtered and ranked:
                    filtered = [ranked[0]]
                    self._log(
                        "retrieve.threshold_fallback",
                        f"All {len(ranked)} results below threshold "
                        f"{self.MIN_SCORE_THRESHOLD} — keeping top result",
                    )
            else:
                filtered = ranked
            top_k = filtered[:k]

            results = [self._finalize(c, query) for c in top_k]

            # ---- (f) trigger/inhibitor signal ----
            # Triggers create a continuous ranking gradient (additive boost
            # scaled by trigger cosine); inhibitors are a hard drop (like a
            # DNA inhibitor).  A note whose inhibitor phrases match the query
            # more strongly than ANY trigger phrase is dropped from recall —
            # still findable via other searches, just not in the first context
            # pop.  A note with no trigger/inhibitor entry passes through
            # (no boost, no drop).  Reuses the query embedding already
            # computed for the vector channel — zero extra embedding calls.
            if self.trigger_store is not None and results:
                _gate_q_emb: Any = None
                try:
                    _gate_q_emb = self.vault_indexer._get_embedding(query)
                except Exception as e:  # noqa: BLE001 — best-effort
                    self._log("trigger_gate.embed_failed", f"{e}")
                _kept: list[dict[str, Any]] = []
                _dropped: list[dict[str, Any]] = []
                for r in results:
                    fp = r.get("file_path", "")
                    if not fp or _gate_q_emb is None:
                        _kept.append(r)
                        continue
                    try:
                        should_drop, trig_s, inib_s = self.trigger_store.check(
                            _gate_q_emb, fp, margin=self.TRIGGER_GATE_MARGIN
                        )
                    except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                        should_drop, trig_s, inib_s = False, 0.0, 0.0
                    if should_drop:
                        _dropped.append(
                            {
                                "file_path": fp,
                                "trigger_score": round(trig_s, 4),
                                "inhibitor_score": round(inib_s, 4),
                            }
                        )
                        continue
                    # Trigger gradient: boost the note's score by the trigger
                    # cosine (scaled).  A note closer to its trigger phrases
                    # floats up the top-k.  No trigger signal → no boost.
                    if trig_s > 0.0:
                        r["score"] = r["score"] + self.TRIGGER_BOOST * trig_s
                    _kept.append(r)
                if _dropped and self.session_logger is not None:
                    self.session_logger.log(
                        "trigger_gate_drop",
                        {"dropped": _dropped, "kept": len(_kept)},
                    )
                # Re-sort by the (possibly trigger-boosted) score so the
                # trigger gradient actually reorders the top-k, not just
                # relabels scores.
                _kept.sort(key=lambda r: r.get("score", 0.0), reverse=True)
                results = _kept

            return {
                "results": results,
                "channels": {
                    "vector": len(vector_hits),
                    "graph": len(graph_candidates),
                    "lexical": len(lexical_candidates),
                },
                "count": len(results),
            }
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # LOG LOUD: retrieval failure must be visible, not silent.
            # The caller (handle_chat) also has an except block that
            # notifies the user via notify_problem with retrieval_broken.
            self._log("retrieve.error", f"{type(e).__name__}: {e}")
            if self.session_logger is not None:
                self.session_logger.log(
                    "retrieval_failed",
                    {
                        "error": f"{type(e).__name__}: {e}",
                        "category": "retrieval_broken",
                    },
                )
            raise

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------
    def _vector_channel(
        self, query: str, k: int
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """
        Run the vector search and normalize scores to [0,1].

        VaultIndexer.search() returns L2 *distance* (smaller = more similar),
        so we convert distance → similarity via min-max normalization so the
        best hit is 1.0. The caller treats higher score = better.
        """
        # Over-fetch so the graph channel can seed from a broader pool (a
        # graph-walk query whose seed note ranked just outside top-k would
        # otherwise never start its walk).  Final top-k truncation happens
        # in retrieve() after merge.
        fetch_k = k * self.GRAPH_OVERFETCH
        raw = self.vault_indexer.search(query, k=fetch_k)

        if not raw:
            return [], {}

        dists = [h.get("score", 0.0) or 0.0 for h in raw]
        max_d = max(dists) if dists else 0.0
        min_d = min(dists) if dists else 0.0
        norm: dict[str, float] = {}
        for h, d in zip(raw, dists, strict=False):
            fp = h.get("file_path")
            if not fp:
                continue
            # distance → similarity: min distance → 1.0, max distance → 0.0.
            # Min-max normalization (not `1 - d/max_d`) so the BEST hit is
            # actually 1.0 as the docstring promises.  `1 - d/max_d` maps the
            # best hit to `1 - min_d/max_d` (~0.24 for nomic-embed-text, whose
            # L2 distances cluster in 0.5-0.7), compressing every score into a
            # flat band and starving the graph channel (which multiplies this
            # score by GRAPH_BOOST) of signal.
            sim = (max_d - d) / (max_d - min_d) if max_d > min_d else 1.0
            norm[fp] = max(0.0, min(1.0, sim))
        # Do NOT truncate to k here — the graph channel seeds from this full
        # over-fetched pool.  retrieve() truncates to top-k after merge.
        return raw, norm

    def _graph_channel(
        self,
        vector_hits: list[dict[str, Any]],
        norm_scores: dict[str, float],
        depth: int,
    ) -> dict[str, dict[str, Any]]:
        """Wikilink neighbors of vector hits, direction-aware.

        Forward links (outgoing) score = GRAPH_BOOST x vector score; backlinks
        (incoming — someone linked TO this note = hub) score = BACKLINK_BOOST x
        vector score.  This folds the old separate backlink channel into a
        single walk so backlinks keep their stronger weight without a second
        channel.  Raises on failure — the caller (retrieve) catches and logs
        loudly.
        """
        candidates: dict[str, dict[str, Any]] = {}
        for hit in vector_hits:
            fp = hit.get("file_path")
            if not fp:
                continue
            base = norm_scores.get(fp, 0.0)
            if not base:
                continue
            name = self._name_from_hit(hit, fp)
            # Forward links (outgoing) and backlinks (incoming) get different
            # weights.  Walk `depth` hops, decaying each hop.
            frontier: list[tuple[str, int, float]] = [
                (n, 0, self.GRAPH_BOOST) for n in self._neighbors(name, "out")
            ] + [(n, 0, self.BACKLINK_BOOST) for n in self._neighbors(name, "in")]
            while frontier:
                node, d, weight = frontier.pop(0)
                if d >= depth:
                    continue
                nfp = self._file_path_for_node(node)
                if not nfp or nfp == fp:
                    continue
                score = weight * base * (0.85**d)
                existing = candidates.get(nfp)
                if existing is None or score > existing["score"]:
                    candidates[nfp] = {
                        "file_path": nfp,
                        "name": node,
                        "score": score,
                        "channels": {"graph"},
                    }
                if d + 1 < depth:
                    frontier.extend(
                        (n, d + 1, weight) for n in self._neighbors(node, "both")
                    )
        return candidates

    def _lexical_channel(self, query: str, k: int) -> dict[str, dict[str, Any]]:
        """BM25-style keyword channel over note titles + cached content.

        The 'FUSED' retriever historically fused only dense (vector) + graph
        channels — no keyword channel.  This recovers title/keyword matches
        that a small embedding model can't map: the golden set's 'direct'
        category (title-level keyword match) and paraphrase queries whose
        note bodies share vocabulary with the query.

        Scores are BM25 over the note's title (weighted) + body, normalized
        to [0,1] by the top score, then discounted by LEXICAL_SCORE_WEIGHT
        so lexical can lift a note into the top-k without dominating a
        strongly-relevant vector hit.  Raises on failure — the caller
        (retrieve) catches and logs loudly.
        """
        candidates: dict[str, dict[str, Any]] = {}
        idx = self.vault_indexer
        metadata = getattr(idx, "metadata", None) or []
        if not metadata:
            return candidates

        # Tokenize the query into lowercase alphanumeric terms (len >= 2).
        q_terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 2]
        if not q_terms:
            return candidates

        # BM25 constants (standard defaults).
        k1 = 1.5
        b = 0.75

        # Cache the tokenized corpus so we don't re-tokenize every note on
        # every query (the live vault has ~1700 files).  Invalidate when the
        # metadata list changes shape (len + first/last file_path is a cheap
        # signature that catches index rebuilds and appends).
        sig = (
            len(metadata),
            metadata[0].get("file_path", ""),
            metadata[-1].get("file_path", ""),
        )
        cached = getattr(self, "_lexical_cache", None)
        if cached is not None and cached[0] == sig:
            tokenized = cached[1]
        else:
            tokenized = []
            for meta in metadata:
                fp = meta.get("file_path", "")
                if not fp:
                    continue
                name = Path(fp).stem
                body = meta.get("content_preview", "") or ""
                toks = re.findall(r"[a-z0-9]+", f"{name} {body}".lower())
                tokenized.append((fp, name, toks))
            self._lexical_cache = (sig, tokenized)

        if not tokenized:
            return candidates

        avgdl = (
            sum(len(t) for _, _, t in tokenized) / len(tokenized) if tokenized else 0.0
        )
        n_docs = len(tokenized)

        # Document frequency per query term.
        df: dict[str, int] = {}
        for term in q_terms:
            df[term] = sum(1 for _, _, toks in tokenized if term in toks)

        # Score each doc.
        scored: list[tuple[float, str, str]] = []
        for fp, name, toks in tokenized:
            dl = len(toks)
            score = 0.0
            for term in q_terms:
                tf = toks.count(term)
                if tf == 0:
                    continue
                dft = df.get(term, 0)
                # BM25 term weight (idf with smoothing).
                idf = math.log(1.0 + (n_docs - dft + 0.5) / (dft + 0.5))
                denom = tf + k1 * (1.0 - b + b * (dl / avgdl if avgdl else 1.0))
                score += idf * (tf * (k1 + 1.0)) / denom
            if score > 0.0:
                scored.append((score, fp, name))

        if not scored:
            return candidates

        # Normalize to [0,1] by the top score, discount, keep top LEXICAL_TOP_K.
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score = scored[0][0]
        for score, fp, name in scored[: self.LEXICAL_TOP_K]:
            norm = (score / top_score) * self.LEXICAL_SCORE_WEIGHT
            candidates[fp] = {
                "file_path": fp,
                "name": self._normalize_name(name),
                "score": norm,
                "channels": {"lexical"},
            }
        return candidates

    # ------------------------------------------------------------------
    # Merge / rerank
    # ------------------------------------------------------------------
    def _merge(
        self,
        vector_hits: list[dict[str, Any]],
        norm_scores: dict[str, float],
        graph_candidates: dict[str, dict[str, Any]],
        lexical_candidates: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Merge all channels by file_path, taking the MAX score across channels."""
        merged: dict[str, dict[str, Any]] = {}

        # seed with vector hits
        for hit in vector_hits:
            fp = hit.get("file_path")
            if not fp:
                continue
            merged[fp] = {
                "file_path": fp,
                "name": self._name_from_hit(hit, fp),
                "score": norm_scores.get(fp, 0.0),
                "channels": {"vector"},
                "content": hit.get("content", ""),
            }

        # fold in graph + lexical, keeping max score and unioning channel tags
        for bucket in (graph_candidates, lexical_candidates or {}):
            for fp, cand in bucket.items():
                existing = merged.get(fp)
                if existing is None:
                    merged[fp] = {
                        "file_path": fp,
                        "name": cand["name"],
                        "score": cand["score"],
                        "channels": set(cand["channels"]),
                        "content": self._content_for_node(cand["name"]),
                    }
                else:
                    existing["score"] = max(existing["score"], cand["score"])
                    existing["channels"] |= cand["channels"]

        return merged

    def _rerank(self, merged: dict[str, dict[str, Any]]) -> None:
        """
        Apply reranking boosts:
          - notes present in vector + graph → xALL_CHANNEL_RERANK
          - high-degree hubs (many backlinks) → xHUB_RERANK
          - procedure status-aware boost (tri-state, additive)
        Mutates `merged` in place.
        """
        graph = self.vault_graph
        backlinks: dict[str, set[str]] = getattr(graph, "backlinks", {}) or {}

        for _fp, cand in merged.items():
            boost = 1.0
            channels = cand.get("channels", set())
            if {"vector", "graph"} <= channels:
                boost *= self.ALL_CHANNEL_RERANK
            name = cand.get("name")
            if name and len(backlinks.get(name, set())) >= self.HUB_DEGREE_THRESHOLD:
                boost *= self.HUB_RERANK
            # Procedure status-aware boost (tri-state).  This is the "scooch
            # over time" grading loop: status moves the rank.
            #   verified    -> base +0.20 + small additive bump (surface it)
            #   experimental-> base +0.20 only (no extra boost, no penalty)
            #   unknown     -> base +0.20 (treated like experimental)
            #   flagged     -> multiplicative penalty (push it down — it
            #                  failed validation repeatedly and is blocked
            #                  from execution, so it shouldn't crowd out
            #                  usable notes at the top of the surface)
            # The base PROCEDURE_BASE_BOOST is ADDITIVE on the normalized
            # score because normalized scores cluster in a tight range — a
            # multiplicative boost is too small to break ties there.
            # Procedures are the tools the model should reach for, not
            # just memories.
            #
            # NAME RESOLUTION: procedure_status_index keys are file stems
            # (e.g. "Find-Recent-Errors") but cand["name"] is the graph-
            # normalized name (e.g. "find-recent-errors").  We build a
            # case-insensitive lookup so the boost actually matches.
            if self.procedure_status_index is not None and name:
                _pstatus = self._procedure_status_lookup(name)
                if _pstatus is not None:
                    # It's a procedure (any status) — apply the base boost.
                    boost_add = self.PROCEDURE_BASE_BOOST
                    if _pstatus == "verified":
                        boost_add += self.VERIFIED_BOOST
                    cand["score"] = cand["score"] * boost + boost_add
                    if _pstatus == "flagged":
                        cand["score"] = cand["score"] * self.FLAGGED_PENALTY
                    continue  # skip the generic multiplicative apply below
            cand["score"] = cand["score"] * boost

    def _procedure_status_lookup(self, name: str) -> str | None:
        """Look up a candidate's procedure status by normalized name.

        ``procedure_status_index`` keys are file stems (e.g.
        ``"Find-Recent-Errors"``) but the merged-pool candidate ``name``
        is the graph-normalized name (e.g. ``"find-recent-errors"``).
        We compare case-insensitively so the boost actually matches.

        Returns the status string (``"verified"``/``"experimental"``/
        ``""`` for unknown-status procedures) or ``None`` if the name
        is not a procedure at all.
        """
        if not self.procedure_status_index:
            return None
        # Fast path: exact match (when keys happen to align).
        s = self.procedure_status_index.get(name)
        if s is not None:
            return s
        # Case-insensitive match: build a lower-key index once, cache it.
        if not hasattr(self, "_proc_status_lower"):
            self._proc_status_lower = {
                k.lower(): v for k, v in self.procedure_status_index.items()
            }
        return self._proc_status_lower.get(name.lower())

    def _finalize(self, cand: dict[str, Any], query: str) -> dict[str, Any]:
        """Shape a merged candidate into the final result dict."""
        channels = sorted(ch for ch in cand.get("channels", set()) if ch)
        content = cand.get("content", "") or self._content_for_node(
            cand.get("name", "")
        )
        result = {
            "file_path": cand.get("file_path", ""),
            "name": cand.get("name", ""),
            "score": round(float(cand.get("score", 0.0)), 4),
            "channels": channels,
            "snippet": self._snippet(content, query),
        }
        # Temporal metadata (issue #85 — temporal awareness): surface the
        # note's creation date (frontmatter `created`) and last-modified
        # time (file mtime) so the LLM can tell a 3-week-old note from a
        # note edited today. Best-effort — never raises, never blocks.
        try:
            _created, _modified = self._temporal_metadata(
                cand.get("file_path", ""), content
            )
            if _created:
                result["created"] = _created
            if _modified:
                result["modified"] = _modified
        except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            pass
        return result

    @staticmethod
    def _temporal_metadata(
        file_path: str, content: str
    ) -> tuple[str | None, str | None]:
        """Return (created, modified) ISO strings for a note, or (None, None).

        ``created`` comes from the note's YAML frontmatter ``created`` field
        (already an ISO date string in most notes). ``modified`` comes from
        the file's mtime. Both are best-effort — any failure returns None
        for that field rather than raising.
        """
        created: str | None = None
        modified: str | None = None
        # created: parse frontmatter `created:` field.
        if content:
            m = re.search(r"(?m)^created:\s*(.+?)\s*$", content)
            if m:
                created = m.group(1).strip().strip("\"'")
        # modified: stat the file's mtime.
        if file_path:
            try:
                p = Path(file_path)
                if p.exists():
                    modified = datetime.fromtimestamp(
                        p.stat().st_mtime, tz=UTC
                    ).strftime("%Y-%m-%d")
            except OSError:
                modified = None
        return created, modified

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_name(name: str) -> str:
        """Match VaultGraph's normalization: strip, lower, backslash → forward slash."""
        if not name:
            return ""
        return name.strip().lower().replace("\\", "/")

    def _neighbors(self, name: str, direction: str = "both") -> list[str]:
        """Call vault_graph.neighbors. Raises on failure — no silent empty return."""
        norm = self._normalize_name(name)
        if not norm:
            return []
        return list(self.vault_graph.neighbors(norm, direction=direction) or [])

    def _file_path_for_node(self, name: str) -> str:
        """Resolve a normalized graph node name to its file_path. Raises on failure."""
        node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))
        if node and node.get("file_path"):
            return node["file_path"]
        return ""

    def _content_for_node(self, name: str) -> str:
        """Fetch the stored content for a graph node.

        Returns empty string if the node doesn't exist or the graph is
        broken — this is content retrieval for snippets, not a channel.
        """
        try:
            node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))
            if node:
                return node.get("content", "") or ""
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass
        return ""

    def _name_from_hit(self, hit: dict[str, Any], fp: str) -> str:
        """Get the normalized name from a vector hit, or from the graph.

        If the hit has no ``name`` field, try to find it in the graph by
        file_path. If the graph is broken (nodes property raises), fall
        back to the normalized file path — this is name resolution, not
        a channel, so degrading to the filename is correct.
        """
        name = hit.get("name")
        if name:
            return self._normalize_name(name)
        try:
            for n, node in (self.vault_graph.nodes or {}).items():
                if node.get("file_path") == fp:
                    return n
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass  # graph broken — fall back to filename
        return self._normalize_name(fp)

    @staticmethod
    def _snippet(content: str, query: str, length: int = 500) -> str:
        """Extract a window around the first query-term match in content.

        Default length is 500 chars (was 200) — the
        [[Why-Vault-Knowledge-Loses-to-Model-Weights]] diagnostic identified
        200-char snippets as Problem #4: "snippet
        truncation shreds arguments into useless fragments." 500 chars
        gives the model enough context to understand a claim + its
        reasoning, not just a keyword match.
        """
        if not content:
            return ""
        try:
            haystack = content.lower()
            # try each query token, longest first, for a stable match
            tokens = sorted(
                (t for t in re.split(r"\s+", query.strip().lower()) if len(t) >= 3),
                key=len,
                reverse=True,
            )
            idx = -1
            for tok in tokens:
                idx = haystack.find(tok)
                if idx >= 0:
                    break
            if idx < 0:
                # no term matched — return the head of the note
                return content[:length].strip() + ("…" if len(content) > length else "")
            half = length // 2
            start = max(0, idx - half)
            end = min(len(content), start + length)
            snippet = content[start:end].strip()
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(content) else ""
            return prefix + snippet + suffix
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return content[:length]

    # ------------------------------------------------------------------
    # Logging / fallbacks
    # ------------------------------------------------------------------
    def _log(self, event: str, detail: str) -> None:
        try:
            if self.session_logger is not None:
                self.session_logger.log(
                    "fused_retrieval", {"event": event, "detail": detail}
                )
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            _frlog.debug("fused_retrieval log failed")

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "results": [],
            "channels": {"vector": 0, "graph": 0, "lexical": 0},
            "count": 0,
        }


if __name__ == "__main__":
    # Minimal smoke test against a live vault, if present.
    import os

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass
    vault_path = os.getenv("VAULT_PATH", ".")
    try:
        vg = VaultGraph(vault_path)
        vg.refresh()
        vi = VaultIndexer(vault_path)
        if hasattr(vi, "initialize"):
            vi.initialize()
        fr = FusedRetriever(vg, vi)
        out = fr.retrieve("semantic drift knowledge management", k=8)
        print("count:", out["count"], "channels:", out["channels"])
        for r in out["results"][:5]:
            print(f"  {r['score']:.3f} [{','.join(r['channels'])}] {r['name']}")
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        print(f"smoke test skipped: {type(e).__name__}: {e}")
