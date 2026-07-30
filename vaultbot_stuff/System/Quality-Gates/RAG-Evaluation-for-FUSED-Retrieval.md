---
created: 2026-07-26
summary: "How to measure whether VaultBot's FUSED retrieval (vector + wikilink graph + backlinks) returns the right notes — and whether the synthesized answer actually uses them."
tags: [architecture, evaluation, rag, retrieval, metrics]
---

# RAG Evaluation for FUSED Retrieval

## The Problem

VaultBot's retrieval system (FUSED: vector + wikilink graph + backlinks) is the core of how it finds relevant notes. But there's no way to know if it's *good*. When the operator asks a question, does the retrieved subgraph contain the notes that would give the best answer? Are we retrieving too much noise? Too little context? We don't know because we don't measure.

This is the hermeneutic layer gap from [[Self-Assessment-Using-the-Knowledge-Triad]]: without evaluation, we can't know if interpretation quality is improving or degrading as the vault grows.

## What the Research Says

RAG evaluation splits into two independent dimensions [sources: A complete guide to RAG evaluation: metrics, testing and best practices, RAG Evaluation Metrics: Recall@K, MRR, Faithfulness and RAGAS (2026)]:

### Retrieval Quality (Did we get the right notes?)

| Metric | What it measures | How to compute |
|---|---|---|
| **Recall@k** | Of all relevant notes, what fraction appeared in top-k results? | Need ground-truth relevant notes per query |
| **Precision@k** | Of top-k results, how many are actually relevant? | Same |
| **NDCG** | Ranking quality — are relevant notes ranked higher? | Same |
| **Context relevance** | Is the retrieved context useful for answering the query? | LLM-judged or human-rated |

### Generation Quality (Did the answer use them well?)

| Metric | What it measures | How to compute |
|---|---|---|
| **Faithfulness** | Is every claim in the answer supported by retrieved context? | Claim extraction + entailment (see [[Claim-Verification-for-Vault-Notes]]) |
| **Answer relevance** | Does the answer actually address the question? | LLM-judged or human-rated |
| **Context utilization** | What fraction of retrieved notes were actually cited? | Count cited vs. retrieved |

**RAGAS** [sources: RAG Evaluation Metrics: Recall@K, MRR, Faithfulness and RAGAS (2026)] provides a framework combining these. **ARES** [sources: Evaluating Retrieval Quality in Retrieval-Augmented Generation] offers automated evaluation with minimal human labeling.

The key insight: **retrieval and generation quality are decoupled** [sources: Retrieval-Augmented Generation: A Comprehensive Survey of Architectures ...]. Good retrieval doesn't guarantee good answers, and good answers can come from mediocre retrieval. You must measure both.

## How This Applies to VaultBot

### Building a Test Set

The biggest challenge is ground truth — we need queries with known-relevant notes. Two sources:

1. **Chat history** — Every past chat where the operator asked a question and I answered from the vault is a test case. The notes I cited are the "ground truth relevant" set. If I now re-run retrieval for the same query and get different notes, that's a regression.

2. **the operator's corrections** — When the operator says "you missed X" or "that's wrong, it should be Y," that's a retrieval failure signal. Log the query, what I retrieved, and what I should have retrieved.

### Metrics for FUSED Specifically

FUSED combines three retrieval signals. We can evaluate each independently:

| Signal | Metric | How |
|---|---|---|
| **Vector** | Semantic recall | Does vector search find notes that are topically related but not linked? |
| **Wikilink graph** | Structural recall | Does graph traversal find notes that are connected but semantically different? |
| **Backlinks** | Contextual recall | Do backlinks surface notes that reference the query topic? |
| **FUSED combined** | Overall recall | Does the combined score rank the best notes highest? |

## What Needs to Be Built

- A `rag_eval.py` module that:
  - Stores test cases (query, retrieved notes, expected notes, answer quality)
  - Computes recall@k, precision@k, NDCG for FUSED retrieval
  - Logs the operator's corrections as test cases automatically
  - Reports retrieval quality trends over time
- Integration with the chat handler to log retrieval results per query
- A `vault_lint` extension to check answer faithfulness against retrieved context

## Related
- [[Claim-Verification-for-Vault-Notes]] — faithfulness metric is shared between both
- [[Calibration-via-Operator-Feedback]] — the operator's corrections as ground truth
- [[Context-Budgeting-for-Vault-Growth]] — retrieval quality affects what gets truncated
- [[Pre-Thought-Information-Shapes]] — typed edges improve retrieval quality
- [[Vault-Longevity-Architecture]] — why retrieval quality matters at scale
- [[Self-Assessment-Using-the-Knowledge-Triad]] — the gap this fills
