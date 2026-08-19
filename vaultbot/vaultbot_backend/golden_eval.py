"""Golden-set evaluation harness — the objective slop-detector.

This is Phase 0 of the sturdiness plan: a curated set of
``{query -> expected_notes[]}`` pairs that the FUSED retriever is scored
against. It turns "I think retrieval is good" into a number that can be
watched over time and gated in CI.

WHY THIS EXISTS
---------------
Every other phase of the plan (procedure discovery, checkpointing, model
tuning) changes retrieval or context-building. Without a fixed yardstick,
there is no way to know whether a change made retrieval *better* or *worse* —
you'd be relying on vibes. The golden set is that yardstick. It is the
"objective slop-detector" the operator asked for.

DESIGN (offline, deterministic, no LLM)
---------------------------------------
- The golden set lives in ``golden_set.json`` (this directory) as a list of
  ``{"query": str, "expected_notes": [str], "note": str}`` entries.
- ``run_golden_eval(retriever, golden_set, k)`` runs each query through the
  retriever's real ``retrieve()`` and computes recall@k, precision@k,
  NDCG@k, and MRR against the expected notes, using the same normalization
  as ``rag_eval.py`` (path-prefix + case + .md stripped).
- It returns an aggregate report plus per-query rows so a failing query
  points at exactly which expectation broke.
- The harness takes a *retriever instance*, so tests can build a stub
  retriever (no FAISS/Ollama/network) and the CI gate can build the real
  FusedRetriever against a fixture vault. The scoring logic is identical
  either way — that's the point.

CI GATE
-------
``check_regression(report, min_recall)`` returns a pass/fail verdict. The
pytest gate (``tests/test_golden_set.py``) fails the run if aggregate
recall@k drops below the configured floor. This is what stops a retrieval
regression from silently shipping.

Pure stdlib + rag_eval normalization. No LLM calls, no network, no FAISS.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# Reuse the exact note-identifier normalization from rag_eval so golden-set
# scoring matches the live retrieval-quality scoring (no drift between the
# two). If rag_eval's normalization improves, this improves with it.
from rag_eval import RAGEvaluator

_DEFAULT_GOLDEN_PATH = Path(__file__).with_name("golden_set.json")


def load_golden_set(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load the golden set from JSON.

    Args:
        path: Path to the golden-set JSON file. Defaults to
              ``golden_set.json`` next to this module.

    Returns:
        A list of entries, each ``{"query": str, "expected_notes": [str]}``.
        Entries missing a query or with an empty expected list are skipped
        (defensive — a hand-edited file shouldn't crash the gate).
    """
    p = Path(path) if path else _DEFAULT_GOLDEN_PATH
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return []
    entries = raw.get("queries", raw) if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        q = (e.get("query") or "").strip()
        exp = e.get("expected_notes") or []
        if q and exp:
            out.append(
                {
                    "query": q,
                    "expected_notes": list(exp),
                    "note": e.get("note", ""),
                }
            )
    return out


# Normalization helpers — thin wrappers over RAGEvaluator's static methods so
# the golden harness and the live evaluator can never drift apart.
_norm = RAGEvaluator._normalize_note


def _recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = {_norm(n) for n in retrieved[:k]}
    rel = {_norm(n) for n in relevant}
    return len(top & rel) / len(rel)


def _precision_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    top_n = {_norm(n) for n in top}
    rel = {_norm(n) for n in relevant}
    return len(top_n & rel) / len(top_n)


def _ndcg_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    if not relevant:
        return 0.0
    rel = {_norm(n) for n in relevant}
    dcg = 0.0
    for i, note in enumerate(retrieved[:k]):
        if _norm(note) in rel:
            dcg += 1.0 / math.log2(i + 2)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / ideal if ideal else 0.0


def _mrr(retrieved: list[str], relevant: list[str]) -> float:
    rel = {_norm(n) for n in relevant}
    for i, note in enumerate(retrieved):
        if _norm(note) in rel:
            return 1.0 / (i + 1)
    return 0.0


def _retrieved_identifiers(results: list[dict[str, Any]]) -> list[str]:
    """Pull a comparable identifier out of each retrieve() result dict."""
    out: list[str] = []
    for r in results:
        if isinstance(r, dict):
            out.append(r.get("file_path") or r.get("name") or r.get("title") or str(r))
        else:
            out.append(str(r))
    return out


def run_golden_eval(
    retriever: Any,
    golden_set: list[dict[str, Any]] | None = None,
    k: int = 5,
    golden_path: str | Path | None = None,
) -> dict[str, Any]:
    """Score a retriever against the golden set.

    Args:
        retriever: Any object with a ``retrieve(query, k) -> {"results": [...]}``
                   method (the real FusedRetriever or a test stub).
        golden_set: Pre-loaded entries. If None, loaded from ``golden_path``.
        k: The cutoff for recall@k / precision@k / NDCG@k.
        golden_path: Where to load the set from if ``golden_set`` is None.

    Returns:
        A report dict::
            {
              "k": k,
              "query_count": int,
              "evaluated_count": int,   # queries that produced any result
              "aggregate": {"recall_at_k", "precision_at_k", "ndcg_at_k", "mrr"},
              "per_query": [ {query, recall_at_k, ..., "missing": [...] } ],
            }
    """
    entries = golden_set if golden_set is not None else load_golden_set(golden_path)
    per_query: list[dict[str, Any]] = []
    recalls: list[float] = []
    precisions: list[float] = []
    ndcgs: list[float] = []
    mrrs: list[float] = []
    evaluated = 0

    for e in entries:
        query = e["query"]
        expected = e["expected_notes"]
        try:
            # Over-fetch 3x the scoring k so graph/backlink candidates have
            # room to surface. The retriever's internal fusion + reranking
            # truncates to the requested k, so a larger k gives the graph
            # channel room to contribute candidates that would be truncated
            # at k=5. Recall@k is then scored against only the top-k of the
            # returned results — standard retrieval eval practice.
            fetch_k = k * 3
            out = retriever.retrieve(query, k=fetch_k)
            results = out.get("results", []) if isinstance(out, dict) else []
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            results = []
        retrieved = _retrieved_identifiers(results)
        if retrieved:
            evaluated += 1

        r = _recall_at_k(retrieved, expected, k)
        p = _precision_at_k(retrieved, expected, k)
        n = _ndcg_at_k(retrieved, expected, k)
        m = _mrr(retrieved, expected)
        recalls.append(r)
        precisions.append(p)
        ndcgs.append(n)
        mrrs.append(m)

        # Which expected notes did NOT surface? (the actionable part)
        got = {_norm(x) for x in retrieved}
        missing = [x for x in expected if _norm(x) not in got]
        per_query.append(
            {
                "query": query,
                "recall_at_k": round(r, 4),
                "precision_at_k": round(p, 4),
                "ndcg_at_k": round(n, 4),
                "mrr": round(m, 4),
                "retrieved_count": len(retrieved),
                "missing": missing,
            }
        )

    def _avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    return {
        "k": k,
        "query_count": len(entries),
        "evaluated_count": evaluated,
        "aggregate": {
            "recall_at_k": _avg(recalls),
            "precision_at_k": _avg(precisions),
            "ndcg_at_k": _avg(ndcgs),
            "mrr": _avg(mrrs),
        },
        "per_query": per_query,
    }


def check_regression(
    report: dict[str, Any], min_recall: float = 0.5, min_ndcg: float = 0.0
) -> dict[str, Any]:
    """Pass/fail verdict for the CI gate.

    Args:
        report: The dict returned by ``run_golden_eval``.
        min_recall: Minimum acceptable aggregate recall@k (0..1).
        min_ndcg: Optional minimum aggregate NDCG@k (0 disables that check).

    Returns:
        ``{"passed": bool, "reasons": [str], "aggregate": {...}}``.
    """
    agg = report.get("aggregate", {})
    reasons: list[str] = []
    recall = agg.get("recall_at_k", 0.0)
    ndcg = agg.get("ndcg_at_k", 0.0)
    if report.get("query_count", 0) == 0:
        reasons.append("golden set is empty — nothing to gate on")
    if recall < min_recall:
        reasons.append(
            f"aggregate recall@{report.get('k')} {recall:.3f} < floor {min_recall:.3f}"
        )
    if min_ndcg > 0.0 and ndcg < min_ndcg:
        reasons.append(
            f"aggregate ndcg@{report.get('k')} {ndcg:.3f} < floor {min_ndcg:.3f}"
        )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "aggregate": agg,
    }
