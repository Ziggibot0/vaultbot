---
type: procedure
status: active
model_cartridge: small
created: 2026-08-04
description: "Evaluate FUSED retrieval quality: run test queries against vault_search, compute recall@k and precision@k, detect regressions, and dispatch conditional fixes. 5 steps with conditional post-evaluation branches. Backed by RAG evaluation metrics research. Idempotent — safe to re-run with same test set."
when_to_use: when checking if vault_search returns the right notes, after significant vault changes, when the operator reports missing notes, when testing retrieval parameter changes, or during periodic quality audits
falsifiable_if: retrieval is rated as high-quality by this procedure but the operator consistently reports missing notes that should have been retrieved
applies_to:
  - retrieval
  - evaluation
  - rag
  - quality-assurance
depends_on:
  - "[[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]"
  - "[[Smart-Vault-Search]]"
  - "[[Self-Assessment-Using-the-Knowledge-Triad]]"
sources:
  - "https://www.evidentlyai.com/llm-guide/rag-evaluation"
  - "https://arxiv.org/abs/2404.13781"
  - "https://langcopilot.com/posts/2025-09-17-rag-evaluation-101-from-recall-k-to-answer-faithfulness"
allowed_tools:
  - vault_search
  - vault_list
  - code_read
  - llm_generate
  - run_procedure
summary: Evaluate-Retrieval
tags:
  - procedure
  - procedures
---

# Evaluate-Retrieval

## When to Run This

Run this when you want to assess whether FUSED retrieval is returning the
right notes. Use cases:
- Periodic quality checks (monthly)
- After significant vault changes (new notes, restructured links)
- When the operator reports that a relevant note was missing from an answer
- When testing changes to FUSED retrieval parameters

## Research Backing

> **Design decisions in this procedure are backed by research:**
>
> - **Recall@k and precision@k as primary metrics:** The
>   [[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]
>   research note shows that recall (did we find the right notes?) and
>   precision (were the retrieved notes actually relevant?) are the two
>   foundational retrieval metrics. RAGAS, ARES, and TruLens all build on
>   these. We use them because they're computable without ground-truth
>   labels beyond a known-expected set.
> - **Regression detection over absolute scores:** The research shows that
>   relative comparisons (did quality drop from last run?) are more
>   actionable than absolute thresholds. We log results and compare across
>   runs.
> - **Conditional dispatch after evaluation:** The
>   [[Execution-Loop-Dominance-Pattern]] demonstrates that routing
>   evaluation results to specialized fixers produces better outcomes than
>   monolithic processing. Low recall → research missing topics. Low
>   precision → re-rank with Smart-Vault-Search. Both low → structural
>   audit.

## Steps

### Step 1: Select Test Queries

Use past chat queries where you know which notes should have been
retrieved. Start with 5-10 queries with clear expected results. The test
set grows organically — add a query every time the operator reports a
missing note.

1. ```python
import json

# Standard test set — queries with known expected results from the vault.
# These are seeded from the operator's corrections and known good retrievals.
# Grow this set organically by adding queries where the operator reported
# that a note was missing from an answer.
test_queries = [
    {"query": "how does VaultBot research work", "expected": ["research_engine", "FUSED-Retrieval", "fused_retrieval"]},
    {"query": "what is the autonomy directive", "expected": ["Autonomy-Directive"]},
    {"query": "how to write a procedure", "expected": ["How-to-Create-a-Procedure", "Dream-Pass"]},
    {"query": "what is the fractal entropy principle", "expected": ["Fractal-Entropy-Principle"]},
    {"query": "how does claim verification work", "expected": ["Claim-Verification-for-Vault-Notes", "Verify-Claims"]},
    {"query": "what is deterministic scaffolding for small models", "expected": ["Deterministic-Scaffolding-for-Small-Models"]},
    {"query": "how does the dream pass work", "expected": ["Dream-Pass"]},
    {"query": "what are the quality gates", "expected": ["Quality-Gates"]},
]

result = json.dumps({
    "status": "ok",
    "test_count": len(test_queries),
    "queries": [q["query"] for q in test_queries],
})
```

[validate: at_least 1 test queries]

### Step 2: Run FUSED Retrieval for Each Query

Execute `vault_search` for each test query and record the top-k results
with scores. The LLM caller must execute these searches — the procedure
framework doesn't auto-call vault_search from within code steps.

2. ```python
import json

_step1 = json.loads(prior_results[-1]) if prior_results else {}
test_queries = _step1.get("queries", [])
k = 5  # top-k to evaluate

result = json.dumps({
    "status": "retrieval_required",
    "k": k,
    "queries_to_run": len(test_queries),
    "action": f"Call vault_search for each of the {len(test_queries)} queries with k={k}. Record the returned file paths and scores for Step 3.",
    "instructions": [
        "For each query in the test set, call vault_search(query=<query>, k=5)",
        "Record: query, expected_notes, retrieved_notes (list of file stems), retrieved_scores",
        "Pass all results to Step 3 as a JSON array",
    ],
})
```

### Step 3: Compute Recall@k

For each query, what fraction of expected notes appeared in the top-k
results? recall = found_expected / total_expected.

3. ```python
import json

# The caller provides retrieval_results as a JSON array:
# [{"query": "...", "expected": ["Note1", "Note2"], "retrieved": ["Note3", "Note1", ...], "scores": [0.9, 0.8, ...]}]
#
# Formula: recall@k = |expected ∩ retrieved| / |expected|

retrieval_results = args.get("retrieval_results", [])
if not retrieval_results:
    retrieval_results = json.loads(args.get("retrieval_results_json", "[]"))

results = []
total_recall = 0
for r in retrieval_results:
    expected = set(r.get("expected", []))
    retrieved = set(r.get("retrieved", []))
    found = expected & retrieved
    recall = len(found) / len(expected) if expected else 1.0
    total_recall += recall
    results.append({
        "query": r.get("query", ""),
        "recall": round(recall, 3),
        "found": list(found),
        "missed": list(expected - retrieved),
    })

avg_recall = total_recall / len(results) if results else 0

result = json.dumps({
    "status": "ok",
    "avg_recall": round(avg_recall, 3),
    "per_query": results,
    "threshold": 0.7,
    "action": "If avg_recall < 0.7, retrieval is missing too many expected notes. See conditional branches.",
})
```

[validate: contains "recall"]

### Step 4: Compute Precision@k

Of the top-k results, how many were actually relevant? precision =
relevant_retrieved / total_retrieved. The small model judges relevance
since "relevant" is subjective — a note might be tangentially related
but not what the query needed.

4. ```python
import json

# Formula: precision@k = |relevant ∩ retrieved| / |retrieved|
# The small model judges which retrieved notes are actually relevant to the query.
# This is an LLM step — the model reads each retrieved note's title/tags and
# judges relevance.

_step3 = json.loads(prior_results[-1]) if prior_results else {}
retrieval_results = args.get("retrieval_results", [])
if not retrieval_results:
    retrieval_results = json.loads(args.get("retrieval_results_json", "[]"))

result = json.dumps({
    "status": "llm_judge_required",
    "formula": "precision@k = |relevant ∩ retrieved| / |retrieved|",
    "threshold": 0.5,
    "action": "For each query, the small model reads retrieved note titles/tags and judges which are truly relevant to the query. Divide relevant count by k. If below 0.5, flag as low precision (too much noise).",
    "instructions": [
        "For each query's retrieved results, judge each note: is it relevant to the query?",
        "Count relevant notes / k = precision@k for that query",
        "Average across all queries for overall precision",
        "Report: avg_precision, per_query precision, and which notes were noise",
    ],
})
```

### Step 5: Check for Regressions and Report

Compare current metrics to previous runs. If recall or precision dropped,
investigate what changed. Report findings to the operator.

5. ```python
import json

# Compare to previous run if available
# The caller should check for a previous eval log in the vault

result = json.dumps({
    "status": "report_required",
    "checks": [
        "Compare current recall@k to previous run — did it drop?",
        "Compare current precision@k to previous run — did it drop?",
        "If recall dropped: were notes deleted? Links changed? Index stale?",
        "If precision dropped: new notes adding noise? Threshold too low?",
        "Log the operator's corrections as new test cases for future runs.",
    ],
    "action": "Report findings to the operator. If retrieval quality is degrading, give specific examples. Don't silently let the system degrade.",
    "log_to": "Create or append to System/Quality-Gates/Retrieval-Eval-Log.md with date, avg_recall, avg_precision, and any regressions.",
})
```

[validate: contains "report"]

## Conditional Branches (Post-Evaluation Dispatch)

> **Research backing:** The
> [[Execution-Loop-Dominance-Pattern]] demonstrates that routing
> evaluation results to specialized fixers produces better outcomes than
> monolithic processing. [[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]]
> shows that feeding evaluation results back into targeted improvement
> processes creates compounding quality gains. This is the
> [[Procedure-Composition-Patterns]] approach: evaluate once, dispatch
> conditionally.

After Step 5 produces the evaluation report, the caller should dispatch
to a fixer procedure based on which metric failed. These are
**conditional if-branches**, not sequential steps.

### IF avg_recall < 0.7 (missing expected notes)

→ Run `run_procedure("Find-Note-Gaps", note_path=<note_that_should_have_been_found>)`
to check if the missing note exists but isn't being retrieved, or if it
genuinely doesn't exist yet.

**Sub-branch:** If the note doesn't exist → the gap should be filled via
`vault_research` or manual creation. If the note exists but isn't
retrieved → the embedding index may be stale; suggest re-indexing or
check if the note's tags/links are insufficient for FUSED retrieval to
surface it.

**Rationale:** Low recall means the vault has knowledge that retrieval
can't find. This is either a missing note (knowledge gap) or a retrieval
failure (index/embedding issue). [[Find-Note-Gaps]] distinguishes
between the two. Backed by
[[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]
which shows that recall failures require investigation into both the
corpus and the retrieval mechanism.

### IF avg_precision < 0.5 (too much noise)

→ Run `run_procedure("Smart-Vault-Search", query=<failing_query>)` to
re-rank results using the small model's content-aware judgment instead
of raw embedding similarity.

**Rationale:** Low precision means retrieval is returning notes that
aren't relevant. [[Smart-Vault-Search]] re-ranks by reading actual note
content, which filters out keyword-overlap false positives. Backed by
the research showing that re-ranking with content-aware models improves
precision without sacrificing recall.

### IF both recall AND precision are low

→ Run `run_procedure("Self-Assessment-Using-the-Knowledge-Triad",
   note_path=<a representative failing note>)` to check if the vault's
   knowledge structure itself is the problem (poor connectivity, thin
   notes, missing links).

**Rationale:** When both metrics fail, the issue is structural — the
vault's graph isn't well-connected enough for FUSED retrieval to
traverse effectively. [[Self-Assessment-Using-the-Knowledge-Triad]]
evaluates the vault's knowledge quality on three axes, which surfaces
structural issues that simple metric checks miss. Backed by
[[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]
which shows that retrieval quality depends on corpus structure, not
just the retrieval algorithm.

### IF no regressions detected (all metrics stable or improving)

→ Log results and report "retrieval quality is stable" to the operator.
No action needed. This branch exists to prevent unnecessary work when
the system is healthy.

**Rationale:** The
[[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]]
research shows that feedback loops must include a "no change needed"
state to avoid unnecessary interventions. Over-correcting a healthy
system introduces noise.

## Falsifiability

If retrieval is rated as high-quality by these metrics but the operator
consistently reports missing notes, the metrics are wrong, not the
operator. Log it as a procedure failure and add the operator's query as
a new test case. The test set grows from corrections — it can never
become stale as long as the operator's feedback is logged.

## Composition

This procedure composes with:
- [[Smart-Vault-Search]] — called when precision is low (re-rank results)
- [[Find-Note-Gaps]] — called when recall is low (check if notes exist)
- [[Self-Assessment-Using-the-Knowledge-Triad]] — called when both are low (structural audit)
- [[Structure-Research-Note]] — called after creating missing notes found during recall investigation