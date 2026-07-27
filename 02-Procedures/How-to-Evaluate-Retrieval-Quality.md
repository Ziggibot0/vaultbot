---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "retrieval is rated as high-quality by this procedure but Sean reports that key notes were missing from the retrieved subgraph"
applies_to:
  - retrieval
  - evaluation
  - rag
depends_on:
  - "[[RAG-Evaluation-for-FUSED-Retrieval]]"
  - "[[How-to-Structure-a-Research-Note]]"
sources:
  - "https://www.evidentlyai.com/llm-guide/rag-evaluation"
  - "https://arxiv.org/abs/2404.13781"
  - "https://langcopilot.com/posts/2025-09-17-rag-evaluation-101-from-recall-k-to-answer-faithfulness"
---

# How to Evaluate Retrieval Quality

## When to Use This

Use this procedure when you want to assess whether FUSED retrieval is returning the right notes for a query. This applies to:
- Periodic quality checks (monthly)
- After significant vault changes (new notes, restructured links)
- When Sean reports that a relevant note was missing from an answer
- When testing changes to FUSED retrieval parameters

## Steps

1. **Select test queries.** Use past chat queries where you know which notes *should* have been retrieved. Start with 5-10 queries that have clear expected results.

2. **Run FUSED retrieval for each query.** Record the top-k notes returned and their scores. Note which retrieval signals contributed (vector, graph, backlinks).

3. **Compute recall@k.** For each query, what fraction of expected notes appeared in the top-k results? `recall = (relevant notes retrieved) / (total relevant notes)`. If recall is below 0.7, retrieval is missing things.

4. **Compute precision@k.** For each query, of the top-k results, how many were actually relevant? `precision = (relevant notes retrieved) / (total notes retrieved)`. If precision is below 0.5, retrieval is returning too much noise.

5. **Check for regressions.** Compare current metrics to previous runs. If recall or precision dropped, investigate what changed in the vault (new notes, deleted notes, changed links).

6. **Log Sean's corrections as test cases.** When Sean says "you missed X" or "that should have included Y," add that query + expected note to the test set. This grows the test set organically.

7. **Report findings.** If retrieval quality is degrading, report it to Sean with specific examples. Don't silently let the system degrade.

## Falsifiability

This procedure is falsifiable: if retrieval is rated as high-quality by these metrics but Sean consistently reports missing notes, the metrics are wrong, not Sean. Log it as a procedure failure.

## Related
- [[RAG-Evaluation-for-FUSED-Retrieval]] — the architecture this procedure implements
- [[Calibration-via-Operator-Feedback]] — Sean's corrections grow the test set
- [[Context-Budgeting-for-Vault-Growth]] — retrieval quality affects what gets truncated
- [[Pre-Thought-Information-Shapes]] — typed edges improve retrieval
- [[Procedural-Bootstrap-and-Evolution-Plan]] — where this fits in the evolution roadmap
