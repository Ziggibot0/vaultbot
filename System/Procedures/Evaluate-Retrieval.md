---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-30
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
description: "Evaluate FUSED retrieval quality: run test queries, compute recall@k and precision@k, check for regressions, log results. 5 deterministic steps. Idempotent — safe to re-run with same test set."
falsifiable_if: "retrieval is rated as high-quality by this procedure but Sean reports that key notes were missing from the retrieved subgraph"
applies_to:
  - retrieval
  - evaluation
  - rag
depends_on:
  - "[[RAG-Evaluation-for-FUSED-Retrieval]]"
  - "[[Structure-Research-Note]]"
sources:
  - "https://www.evidentlyai.com/llm-guide/rag-evaluation"
  - "https://arxiv.org/abs/2404.13781"
  - "https://langcopilot.com/posts/2025-09-17-rag-evaluation-101-from-recall-k-to-answer-faithfulness"
allowed_tools:
  - vault_search
  - vault_list
  - code_read
---

# Evaluate-Retrieval

## When to Run This

Run this when you want to assess whether FUSED retrieval is returning the right notes. Use cases:
- Periodic quality checks (monthly)
- After significant vault changes (new notes, restructured links)
- When Sean reports that a relevant note was missing from an answer
- When testing changes to FUSED retrieval parameters

## Steps

### Step 1: Select Test Queries

Use past chat queries where you know which notes should have been retrieved. Start with 5-10 queries with clear expected results.

1. ```python
import json

# Standard test set — queries with known expected results from the vault.
# These are seeded from Sean's corrections and known good retrievals.
# Grow this set organically by adding queries where Sean reported misses.
test_queries = [
    {"query": "how does VaultBot research work", "expected": ["research_engine", "FUSED-Retrieval", "fused_retrieval"]},
    {"query": "what is the autonomy directive", "expected": ["Autonomy-Directive"]},
    {"query": "how to write a procedure", "expected": ["Procedure-Creator", "Dream-Pass"]},
    {"query": "what is the fractal entropy principle", "expected": ["Fractal-Entropy-Principle"]},
    {"query": "how does claim verification work", "expected": ["Claim-Verification-for-Vault-Notes", "Verify-Claims"]},
]

result = json.dumps({
    "status": "ok",
    "test_count": len(test_queries),
    "queries": [q["query"] for q in test_queries],
})
```

[validate: at_least 1 test queries]

### Step 2: Run FUSED Retrieval for Each Query

Execute vault_search for each test query and record the top-k results with scores.

2. ```python
import json

_step1 = json.loads(prior_results[-1]) if prior_results else {}
test_queries = [
    {"query": "how does VaultBot research work", "expected": ["research_engine", "FUSED-Retrieval", "fused_retrieval"]},
    {"query": "what is the autonomy directive", "expected": ["Autonomy-Directive"]},
    {"query": "how to write a procedure", "expected": ["Procedure-Creator", "Dream-Pass"]},
    {"query": "what is the fractal entropy principle", "expected": ["Fractal-Entropy-Principle"]},
    {"query": "how does claim verification work", "expected": ["Claim-Verification-for-Vault-Notes", "Verify-Claims"]},
]

# The actual vault_search calls are made by the LLM outside the procedure.
# This step documents the requirement and prepares the evaluation framework.
k = 5  # top-k to evaluate

result = json.dumps({
    "status": "retrieval_required",
    "k": k,
    "queries_to_run": len(test_queries),
    "action": f"Call vault_search for each of the {len(test_queries)} queries with k={k}. Record results for Step 3.",
})
```

### Step 3: Compute Recall@k

For each query, what fraction of expected notes appeared in the top-k? recall = relevant_retrieved / total_relevant.

3. ```python
import json

# This step computes recall from the retrieval results.
# The actual results are populated by the LLM after running vault_search.
# Formula: recall@k = (relevant notes in top-k) / (total relevant notes)

# Placeholder — the LLM fills in actual results
recall_formula = "recall@k = |relevant ∩ retrieved| / |relevant|"
threshold = 0.7  # below this, retrieval is missing things

result = json.dumps({
    "status": "compute_required",
    "formula": recall_formula,
    "threshold": threshold,
    "action": "For each query, count how many expected notes appeared in top-k results. Divide by total expected. If below 0.7, flag as low recall.",
})
```

### Step 4: Compute Precision@k

Of the top-k results, how many were actually relevant? precision = relevant_retrieved / total_retrieved.

4. ```python
import json

# Formula: precision@k = |relevant ∩ retrieved| / |retrieved|
threshold = 0.5  # below this, too much noise

result = json.dumps({
    "status": "compute_required",
    "formula": "precision@k = |relevant ∩ retrieved| / |retrieved|",
    "threshold": threshold,
    "action": "For each query, count how many of the top-k results were actually relevant. Divide by k. If below 0.5, flag as low precision (too much noise).",
})
```

### Step 5: Check for Regressions and Report

Compare current metrics to previous runs. If recall or precision dropped, investigate what changed. Report findings to Sean.

5. ```python
import json

# Compare to previous run if available
# The LLM should check rag_eval_log.json for historical baselines

result = json.dumps({
    "status": "report_required",
    "checks": [
        "Compare current recall@k to previous run — did it drop?",
        "Compare current precision@k to previous run — did it drop?",
        "If recall dropped: were notes deleted? Links changed? Index stale?",
        "If precision dropped: new notes adding noise? Threshold too low?",
        "Log Sean's corrections as new test cases for future runs.",
    ],
    "action": "Report findings to Sean. If retrieval quality is degrading, give specific examples. Don't silently let the system degrade.",
})
```

[validate: contains "report"]

## Falsifiability

If retrieval is rated as high-quality by these metrics but Sean consistently reports missing notes, the metrics are wrong, not Sean. Log it as a procedure failure.

## Related

- [[RAG-Evaluation-for-FUSED-Retrieval]] — the architecture this procedure implements
- [[Calibration-via-Operator-Feedback]] — Sean's corrections grow the test set
- [[Context-Budgeting-for-Vault-Growth]] — retrieval quality affects what gets truncated
- [[Procedural-Bootstrap-and-Evolution-Plan]] — where this fits in the evolution roadmap