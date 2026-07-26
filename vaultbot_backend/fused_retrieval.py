"""
Fused retrieval: combine FAISS vector search with the Obsidian wikilink/backlink graph.

Obsidian's link graph is high-precision / low-recall (only linked notes surface),
while dense embeddings are the opposite (high recall, lower precision). Fusing the two
gives both: semantic neighbors seed the graph walk, and the graph walks back in
contextually-connected notes that pure similarity may miss.

Three channels are fused:
  a. Vector channel   — vault_indexer.search() normalized to [0,1]
  b. Graph channel    — wikilink neighbors of vector hits, score = 0.5 × vector score
  c. Backlink channel — backlinks of vector hits, score = 0.7 × vector score
                       (backlinks are stronger: someone linked TO this note = hub)

Candidates are merged by file_path (max score across channels), reranked for
multi-channel agreement (×1.3) and hub-ness (×1.1), then truncated to top-k.

Future work: this is a lightweight stand-in for the hybrid/community-augmented
retrieval described in GraphRAG (arXiv:2404.16130), which builds a community
structure over entities and fuses embedding retrieval with community-level
summaries. LightRAG's dual-level (low-level entity + high-level keyword) retrieval
is the same idea at a different granularity — this module's vector+graph fusion
mirrors the low-level path and leaves the community path as a drop-in extension.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

try:
    from session_logger import SessionLogger
except Exception:  # pragma: no cover - logger is optional
    SessionLogger = None  # type: ignore


class FusedRetriever:
    """Fuse FAISS vector search with the Obsidian link graph."""

    # Channel weight multipliers (from the research design).
    GRAPH_BOOST = 0.5      # forward-link neighbors
    BACKLINK_BOOST = 0.7   # backlinks (hubs)
    ALL_CHANNEL_RERANK = 1.3   # appears in vector + graph + backlink
    HUB_RERANK = 1.1       # high backlink degree
    HUB_DEGREE_THRESHOLD = 3   # min backlinks to count as a hub
    # Drift re-ranking: over-fetch this many candidates from the vector
    # channel, then re-rank with drifted embeddings. 3x gives the drift
    # layer room to promote a note that raw similarity ranked lower but
    # has accumulated helpful feedback for similar queries.
    DRIFT_OVERFETCH = 3
    # How much the drift-adjusted distance can move a candidate's
    # normalized score, as a fraction of the score range.  Keeps drift
    # as a tie-breaker/booster, not a wholesale override of content
    # similarity — a note with strong drift but weak content similarity
    # won't leapfrog a note that's genuinely a better content match.
    DRIFT_SCORE_WEIGHT = 0.25

    def __init__(
        self,
        vault_graph: VaultGraph,
        vault_indexer: VaultIndexer,
        session_logger: SessionLogger | None = None,
        embedding_drift: Any | None = None,
    ) -> None:
        self.vault_graph = vault_graph
        self.vault_indexer = vault_indexer
        self.session_logger = session_logger
        # Optional EmbeddingDrift layer (relevance feedback). When wired in,
        # main.py records helpful/unhelpful signals on it directly after
        # each chat; the retriever stores the reference so future work can
        # over-fetch + re-rank with drifted embeddings. Accepting it here
        # keeps the constructor call in main.py valid even when the
        # retriever-side re-ranking isn't active.
        self.embedding_drift = embedding_drift
        self._log("init", "FusedRetriever initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def retrieve(self, query: str, k: int = 10, depth: int = 1) -> dict[str, Any]:
        """
        Fuse vector + graph + backlink retrieval.

        Returns:
            {
              "results": [
                {"file_path", "name", "score", "channels": [str], "snippet"}, ...
              ],
              "channels": {"vector": N, "graph": N, "backlink": N},
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
            graph_candidates = self._graph_channel(vector_hits, norm_scores, depth)

            # ---- channel (c): backlinks ----
            backlink_candidates = self._backlink_channel(vector_hits, norm_scores)

            # ---- (d) merge + dedup ----
            merged = self._merge(
                vector_hits=vector_hits,
                norm_scores=norm_scores,
                graph_candidates=graph_candidates,
                backlink_candidates=backlink_candidates,
            )

            # ---- (e) rerank ----
            self._rerank(merged)

            # ---- (f) truncate to top-k ----
            ranked = sorted(
                merged.values(), key=lambda c: c["score"], reverse=True
            )[:k]

            results = [self._finalize(c, query) for c in ranked]

            return {
                "results": results,
                "channels": {
                    "vector": len(vector_hits),
                    "graph": len(graph_candidates),
                    "backlink": len(backlink_candidates),
                },
                "count": len(results),
            }
        except Exception as e:  # never crash the chat loop
            self._log("retrieve.error", f"{type(e).__name__}: {e}")
            return self._empty()

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------
    def _vector_channel(
        self, query: str, k: int
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """
        Run the vector search and normalize scores to [0,1].

        VaultIndexer.search() returns L2 *distance* (smaller = more similar),
        so we convert distance → similarity via `1 - d/max_d` and then normalize
        to [0,1] so the best hit is 1.0. The caller treats higher score = better.

        DRIFT RE-RANKING: when an EmbeddingDrift layer is wired in, this
        over-fetches DRIFT_OVERFETCH × k candidates, reconstructs each one's
        content embedding straight from the FAISS index (zero Oollama calls),
        applies the note's accumulated drift vector, and recomputes the L2
        distance to the query embedding.  A note that proved helpful for
        similar past queries drifts TOWARD the query (smaller distance →
        higher score); one that proved unhelpful drifts AWAY.  This re-ranks
        by "what is this note good FOR" (feedback) on top of "what is it
        similar to" (content).  The drift signal is LLM-free — derived from
        the agent's own behavior in handle_chat.
        """
        try:
            # Over-fetch so drift has room to promote a lower-ranked note.
            fetch_k = k
            if self.embedding_drift is not None:
                fetch_k = k * self.DRIFT_OVERFETCH
            raw = self.vault_indexer.search(query, k=fetch_k)
        except Exception as e:
            self._log("vector.error", f"{type(e).__name__}: {e}")
            return [], {}

        if not raw:
            return [], {}

        # --- Drift re-ranking (LLM-free) ---
        # Reconstruct each candidate's content embedding from FAISS, apply
        # its drift vector, recompute L2 distance to the query embedding.
        # If anything fails (no drift layer, empty index, reconstruct
        # unavailable), we fall back to the raw distances — drift is a
        # bonus, never a hard dependency.
        if self.embedding_drift is not None:
            try:
                drifted = self._drift_rerank(raw, query)
                if drifted is not None:
                    raw = drifted
            except Exception as e:
                self._log("vector.drift_rerank_failed",
                          f"{type(e).__name__}: {e} — using raw distances")

        dists = [h.get("score", 0.0) or 0.0 for h in raw]
        max_d = max(dists) if dists else 0.0
        norm: dict[str, float] = {}
        for h, d in zip(raw, dists):
            fp = h.get("file_path")
            if not fp:
                continue
            # distance → similarity: 0 distance → 1.0, max distance → 0.0.
            if max_d > 0:
                sim = 1.0 - (d / max_d)
            else:
                sim = 1.0
            norm[fp] = max(0.0, min(1.0, sim))
        # Truncate to the requested k AFTER drift re-ranking so the promoted
        # notes actually make it into the returned set.
        raw = raw[:k]
        norm = {fp: norm[fp] for fp in (h.get("file_path") for h in raw)
                if fp and fp in norm}
        return raw, norm

    def _drift_rerank(self, raw: list[dict[str, Any]],
                      query: str) -> list[dict[str, Any]] | None:
        """Re-rank `raw` hits by drift-adjusted L2 distance to the query.

        For each candidate: reconstruct its content embedding from the
        FAISS index (zero Ollama calls), apply drift, recompute distance.
        Returns a new list sorted by drift-adjusted distance (ascending =
        most relevant first), or None if drift could not be applied (e.g.
        no query embedding available, index missing) so the caller falls
        back to raw distances.
        """
        idx = self.vault_indexer
        # Get the query embedding ONCE (one Ollama call, reused for all
        # candidates — not one per candidate).
        query_emb = idx._get_embedding(query)
        if query_emb is None or query_emb.size == 0:
            return None

        drifted_hits: list[dict[str, Any]] = []
        any_drifted = False
        for h in raw:
            fp = h.get("file_path", "")
            if not fp:
                continue
            content_emb = idx.reconstruct_embedding(fp)
            if content_emb is None:
                # Can't reconstruct — keep raw distance, no drift applied.
                drifted_hits.append(h)
                continue
            drifted_emb = self.embedding_drift.apply_drift(fp, content_emb)
            if drifted_emb is content_emb:
                # No drift recorded for this note — keep raw distance.
                drifted_hits.append(h)
                continue
            # Recompute L2 distance to the query with the drifted embedding.
            new_dist = float(np.linalg.norm(drifted_emb - query_emb))
            any_drifted = True
            drifted_hits.append({**h, "score": new_dist})

        if not any_drifted:
            return None  # nothing actually drifted — keep raw order

        # Sort by the (possibly drift-adjusted) distance, ascending.
        drifted_hits.sort(key=lambda h: h.get("score", 0.0))
        self._log("vector.drift_rerank",
                  f"re-ranked {len(drifted_hits)} hits, drift applied to "
                  f"{sum(1 for h in drifted_hits if h.get('_drifted'))}")
        return drifted_hits

    def _graph_channel(
        self,
        vector_hits: list[dict[str, Any]],
        norm_scores: dict[str, float],
        depth: int,
    ) -> dict[str, dict[str, Any]]:
        """1-hop wikilink neighbors of vector hits. Score = GRAPH_BOOST × vector score."""
        candidates: dict[str, dict[str, Any]] = {}
        try:
            graph = self.vault_graph
            for hit in vector_hits:
                fp = hit.get("file_path")
                base = norm_scores.get(fp, 0.0)
                if not base:
                    continue
                name = self._name_from_hit(hit, fp)
                neighbors = self._safe_neighbors(name, direction="both")
                # limited walk of `depth` hops
                frontier = [(n, 0) for n in neighbors]
                while frontier:
                    node, d = frontier.pop(0)
                    if d >= depth:
                        continue
                    nfp = self._file_path_for_node(node)
                    if not nfp or nfp == fp:
                        continue
                    score = self.GRAPH_BOOST * base * (0.85 ** d)
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
                            (n, d + 1) for n in self._safe_neighbors(node, "both")
                        )
        except Exception as e:
            self._log("graph.error", f"{type(e).__name__}: {e}")
        return candidates

    def _backlink_channel(
        self,
        vector_hits: list[dict[str, Any]],
        norm_scores: dict[str, float],
    ) -> dict[str, dict[str, Any]]:
        """Backlinks of vector hits. Score = BACKLINK_BOOST × vector score."""
        candidates: dict[str, dict[str, Any]] = {}
        try:
            graph = self.vault_graph
            backlinks: dict[str, set[str]] = getattr(graph, "backlinks", {}) or {}
            for hit in vector_hits:
                fp = hit.get("file_path")
                base = norm_scores.get(fp, 0.0)
                if not base:
                    continue
                name = self._name_from_hit(hit, fp)
                # who links TO this note?
                linked_from = backlinks.get(name, set())
                for src in linked_from:
                    src_fp = self._file_path_for_node(src)
                    if not src_fp or src_fp == fp:
                        continue
                    score = self.BACKLINK_BOOST * base
                    existing = candidates.get(src_fp)
                    if existing is None or score > existing["score"]:
                        candidates[src_fp] = {
                            "file_path": src_fp,
                            "name": src,
                            "score": score,
                            "channels": {"backlink"},
                        }
        except Exception as e:
            self._log("backlink.error", f"{type(e).__name__}: {e}")
        return candidates

    # ------------------------------------------------------------------
    # Merge / rerank
    # ------------------------------------------------------------------
    def _merge(
        self,
        vector_hits: list[dict[str, Any]],
        norm_scores: dict[str, float],
        graph_candidates: dict[str, dict[str, Any]],
        backlink_candidates: dict[str, dict[str, Any]],
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

        # fold in graph + backlink, keeping max score and unioning channel tags
        for bucket in (graph_candidates, backlink_candidates):
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
          - notes present in all 3 channels → ×ALL_CHANNEL_RERANK
          - high-degree hubs (many backlinks) → ×HUB_RERANK
        Mutates `merged` in place.
        """
        try:
            graph = self.vault_graph
            backlinks: dict[str, set[str]] = getattr(graph, "backlinks", {}) or {}
        except Exception as e:
            self._log("rerank.graph_unavailable", f"{type(e).__name__}: {e}")
            backlinks = {}

        for fp, cand in merged.items():
            boost = 1.0
            channels = cand.get("channels", set())
            if {"vector", "graph", "backlink"} <= channels:
                boost *= self.ALL_CHANNEL_RERANK
            name = cand.get("name")
            if name and len(backlinks.get(name, set())) >= self.HUB_DEGREE_THRESHOLD:
                boost *= self.HUB_RERANK
            cand["score"] = cand["score"] * boost

    def _finalize(self, cand: dict[str, Any], query: str) -> dict[str, Any]:
        """Shape a merged candidate into the final result dict."""
        channels = sorted(
            ch for ch in cand.get("channels", set()) if ch
        )
        content = cand.get("content", "") or self._content_for_node(cand.get("name", ""))
        return {
            "file_path": cand.get("file_path", ""),
            "name": cand.get("name", ""),
            "score": round(float(cand.get("score", 0.0)), 4),
            "channels": channels,
            "snippet": self._snippet(content, query),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_name(name: str) -> str:
        """Match VaultGraph's normalization: strip, lower, backslash → forward slash."""
        if not name:
            return ""
        return name.strip().lower().replace("\\", "/")

    def _safe_neighbors(self, name: str, direction: str = "both") -> list[str]:
        """Call vault_graph.neighbors but never raise."""
        try:
            norm = self._normalize_name(name)
            if not norm:
                return []
            return list(self.vault_graph.neighbors(norm, direction=direction) or [])
        except Exception as e:
            self._log("neighbors.error", f"{type(e).__name__}: {e}")
            return []

    def _file_path_for_node(self, name: str) -> str:
        """Resolve a normalized graph node name to its file_path."""
        try:
            node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))
            if node and node.get("file_path"):
                return node["file_path"]
        except Exception as e:
            self._log("resolve.error", f"{type(e).__name__}: {e}")
        return ""

    def _content_for_node(self, name: str) -> str:
        """Fetch the stored content for a graph node."""
        try:
            node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))
            if node:
                return node.get("content", "") or ""
        except Exception as e:
            self._log("content.error", f"{type(e).__name__}: {e}")
        return ""

    def _name_from_hit(self, hit: dict[str, Any], fp: str) -> str:
        """Get the normalized name from a vector hit, falling back to the graph."""
        name = hit.get("name")
        if name:
            return self._normalize_name(name)
        try:
            for n, node in (self.vault_graph.nodes or {}).items():
                if node.get("file_path") == fp:
                    return n
        except Exception:
            pass
        return self._normalize_name(fp)

    @staticmethod
    def _snippet(content: str, query: str, length: int = 200) -> str:
        """Extract a window around the first query-term match in content."""
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
        except Exception:
            return content[:length]

    # ------------------------------------------------------------------
    # Logging / fallbacks
    # ------------------------------------------------------------------
    def _log(self, event: str, detail: str) -> None:
        try:
            if self.session_logger is not None:
                self.session_logger.log("fused_retrieval", {"event": event, "detail": detail})
        except Exception:
            pass

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "results": [],
            "channels": {"vector": 0, "graph": 0, "backlink": 0},
            "count": 0,
        }


if __name__ == "__main__":
    # Minimal smoke test against a live vault, if present.
    import os

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    vault_path = os.getenv("VAULT_PATH", "C:\\Users\\skell\\Desktop\\Vault2")
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
    except Exception as e:
        print(f"smoke test skipped: {type(e).__name__}: {e}")
