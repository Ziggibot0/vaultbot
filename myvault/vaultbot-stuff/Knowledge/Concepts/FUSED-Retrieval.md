---
type: concept
status: active
baseline: true
created: 2026-08-06
tags:
  - retrieval
  - vault-graph
  - FAISS
  - embeddings
  - hybrid-search
summary: "FUSED Retrieval — VaultBot's hybrid retrieval system that combines FAISS vector search with Obsidian's wikilink graph for high-precision, context-rich results."
---

# FUSED-Retrieval

FUSED Retrieval is VaultBot's hybrid retrieval system. It fuses two complementary search strategies — **dense vector search** (FAISS embeddings) and **graph traversal** (Obsidian's wikilink/backlink graph) — to produce results that are both semantically relevant and contextually connected.

## The Problem It Solves

Pure vector search (FAISS) is **high-recall, low-precision**: it finds semantically similar notes but may surface tangentially related content while missing notes that are deeply connected through links but use different vocabulary. Pure graph search is **high-precision, low-recall**: it finds notes that are explicitly linked but misses semantically relevant notes that haven't been linked yet.

FUSED combines both: vector search seeds the graph walk, and the graph walk pulls in contextually-connected notes that pure similarity would miss.

## Three Channels

1. **Vector channel** — FAISS similarity search, scores normalized to [0,1]
2. **Graph channel** — wikilink neighbors of vector hits, score = 0.5 × vector score
3. **Backlink channel** — backlinks of vector hits, score = 0.7 × vector score (backlinks are stronger signals: someone linked TO this note, making it a hub)

Candidates are merged by file path (max score across channels), then reranked:
- **Multi-channel agreement** (×1.3): notes appearing in all three channels get boosted
- **Hub boost** (×1.1): notes with ≥3 backlinks get a hub bonus
- **Verified procedure boost** (+0.05): procedures with `status: verified` get a small bump
- **Procedure base boost**: any `type: procedure` note gets an additive score bump

Results below a minimum score threshold (0.15) are dropped to avoid noise.

## Drift Re-ranking

FUSED also incorporates **drift-adjusted embeddings** — a feedback mechanism that tracks which notes were helpful for similar past queries and adjusts scores accordingly. The vector channel over-fetches 3× candidates, then drift re-ranking promotes notes that have accumulated positive feedback for similar queries. Drift is capped at 25% of the score range so it acts as a tie-breaker, not a wholesale override of content similarity.

## Why It Matters

Without FUSED, retrieval is either blind to link structure (pure vector) or blind to semantic similarity (pure graph). FUSED gives both: the vector channel ensures semantic coverage, the graph channels ensure contextual depth, and the reranking ensures the best of both worlds surface to the top.

## Architecture

The `FusedRetriever` class in `fused_retrieval.py` wraps `VaultIndexer` (FAISS) and `VaultGraph` (wikilink graph). It is called by the chat handler on every user query to build the VAULT CONTEXT block injected into the system prompt.

## Related

- [[vault_cluster_analyzer]] — cluster analysis reveals the community structure that FUSED traverses
- [[Vault-Graph-Analysis]] — broader graph connectedness analysis
- [[RAG-adaptive-retrieval]] — the research lineage: GraphRAG and LightRAG's dual-level retrieval
- [[vault_search]] — the tool interface that calls FUSED retrieval
