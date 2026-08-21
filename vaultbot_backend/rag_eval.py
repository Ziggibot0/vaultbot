"""
RAG Evaluator — measures FUSED retrieval quality using standard RAG metrics.

Computes recall@k, precision@k, NDCG@k, and MRR for retrieval.
Logs every retrieval event (cheap) and computes metrics when
ground truth is available (from the operator's corrections or manual annotation).

Pure deterministic. No LLM calls.

See [[RAG-Evaluation-for-FUSED-Retrieval]] for the architecture rationale.
See [[How-to-Evaluate-Retrieval-Quality]] for the procedural steps.
"""

import json
import math
import os
from datetime import datetime


class RAGEvaluator:
    """Evaluates FUSED retrieval quality using standard RAG metrics.

    Two modes:
    1. Logging (always on, cheap) — records what was retrieved for every query
    2. Evaluation (on-demand) — computes metrics when ground truth is available

    Ground truth sources:
    - the operator's corrections ("you missed X" -> X should have been retrieved)
    - Manual annotation via add_ground_truth()
    - Future: calibration tracker integration (automatic from corrections)
    """

    def __init__(
        self,
        log_path: str | None = None,
        regression_window: int = 10,
        regression_threshold: float = 0.1,
    ):
        """Initialize the RAG evaluator.

        Args:
            log_path: Path to the JSON log file. Defaults to rag_eval_log.json
                       in the same directory as this module.
            regression_window: Number of recent queries to compare against
                                historical baseline for regression detection.
            regression_threshold: Fractional drop that triggers a regression
                                  alert (0.1 = 10% drop).
        """
        self.log_path = log_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "rag_eval_log.json"
        )
        self.regression_window = regression_window
        self.regression_threshold = regression_threshold
        self._ensure_log()

    def _ensure_log(self):
        """Create the log file if it doesn't exist."""
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"retrieval_logs": [], "ground_truth": {}, "metric_history": []},
                    f,
                    indent=2,
                )

    def _load(self) -> dict:
        with open(self.log_path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ─── Logging (always on, cheap) ───

    def log_retrieval(
        self,
        query: str,
        retrieved: list[dict],
        k: int = 5,
        timestamp: str | None = None,
    ) -> None:
        """Log a retrieval event. Called after every FUSED retrieve.

        Args:
            query: The user's query string.
            retrieved: List of retrieved result dicts (from FUSED retriever).
                       Each should have 'file_path' or 'title' and 'score'.
            k: Number of results requested.
            timestamp: ISO timestamp. Defaults to now.
        """
        ts = timestamp or datetime.now().isoformat()
        data = self._load()

        retrieved_notes = []
        for r in retrieved:
            if isinstance(r, dict):
                identifier = r.get("file_path") or r.get("title") or str(r)
                retrieved_notes.append(
                    {
                        "note": identifier,
                        "score": r.get("score", 0),
                        "vector_score": r.get("vector_score", 0),
                        "graph_score": r.get("graph_score", 0),
                        "backlink_score": r.get("backlink_score", 0),
                    }
                )

        data["retrieval_logs"].append(
            {
                "timestamp": ts,
                "query": query,
                "k": k,
                "retrieved": retrieved_notes,
            }
        )
        self._save(data)

    # ─── Ground truth management ───

    def add_ground_truth(self, query: str, relevant_notes: list[str]) -> None:
        """Add or update a ground-truth mapping for a query.

        Ground truth comes from:
        - the operator's corrections ("you missed X" -> X should have been retrieved)
        - Manual annotation (the operator or VaultBot marks expected results)

        Args:
            query: The query string (normalized for matching).
            relevant_notes: List of note identifiers that SHOULD have been
                           retrieved for this query.
        """
        data = self._load()
        key = query.strip().lower()
        data["ground_truth"][key] = relevant_notes
        self._save(data)

    def _find_ground_truth(self, query: str) -> list[str] | None:
        """Look up ground truth for a query. Returns None if not found.

        Tries exact match first, then substring matching (query might be
        longer or shorter than the stored key).
        """
        data = self._load()
        key = query.strip().lower()
        # Exact match first
        if key in data["ground_truth"]:
            return data["ground_truth"][key]
        # Substring match (longer key in query, or query in longer key)
        for stored_key, notes in data["ground_truth"].items():
            if stored_key in key or key in stored_key:
                return notes
        return None

    # ─── Note normalization ───

    @staticmethod
    def _normalize_note(note: str) -> str:
        """Normalize a note identifier to its comparable STEM.

        Notes are identified by their basename (the wikilink stem), not their
        directory path — a wikilink ``[[No-Wikipedia-Directive]]`` resolves to
        the note regardless of which folder it lives in. So comparison must
        strip ALL directory components, not just a few known prefixes.

        Handles every form the pipeline produces:
          - bare stem:            "No-Wikipedia-Directive"
          - relative path:        "vaultbot/System/Identity/No-Wikipedia-Directive.md"
          - absolute path:        "C:/Vault/System/Identity/No-Wikipedia-Directive.md"
          - forward/back slashes on Windows
        All normalize to "no-wikipedia-directive".

        Without the basename strip, ground truth stored as a stem never
        matches a retrieved path in a subfolder (most of the vault), so
        recall@k would read 0 even when retrieval is correct.
        """
        n = note.replace("\\", "/").lower().strip()
        # Strip every directory component — keep only the basename.
        n = n.rsplit("/", 1)[-1]
        if n.endswith(".md"):
            n = n[:-3]
        return n.strip()

    # ─── Metric computation ───

    def _compute_recall_at_k(
        self, retrieved: list[str], relevant: list[str], k: int
    ) -> float:
        """Recall@k: fraction of relevant notes in top-k results."""
        if not relevant:
            return 0.0
        top_k = retrieved[:k]
        retrieved_set = {self._normalize_note(n) for n in top_k}
        relevant_set = {self._normalize_note(n) for n in relevant}
        hits = len(retrieved_set & relevant_set)
        return hits / len(relevant_set)

    def _compute_precision_at_k(
        self, retrieved: list[str], relevant: list[str], k: int
    ) -> float:
        """Precision@k: fraction of top-k results that are relevant."""
        top_k = retrieved[:k]
        if not top_k:
            return 0.0
        retrieved_set = {self._normalize_note(n) for n in top_k}
        relevant_set = {self._normalize_note(n) for n in relevant}
        hits = len(retrieved_set & relevant_set)
        return hits / len(retrieved_set)

    def _compute_ndcg_at_k(
        self, retrieved: list[str], relevant: list[str], k: int
    ) -> float:
        """NDCG@k: normalized discounted cumulative gain.

        Uses binary relevance (1 if relevant, 0 if not).
        DCG = sum(rel_i / log2(i+1)) for i=1..k
        IDCG = DCG with all relevant items ranked first
        NDCG = DCG / IDCG
        """
        if not relevant:
            return 0.0
        top_k = retrieved[:k]
        relevant_set = {self._normalize_note(n) for n in relevant}

        # DCG
        dcg = 0.0
        for i, note in enumerate(top_k):
            rel = 1.0 if self._normalize_note(note) in relevant_set else 0.0
            if rel > 0:
                dcg += rel / math.log2(i + 2)  # i+2 because i is 0-indexed

        # IDCG (ideal: all relevant items at the top)
        ideal_hits = min(len(relevant_set), k)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))

        if idcg == 0:
            return 0.0
        return dcg / idcg

    def _compute_mrr(self, retrieved: list[str], relevant: list[str]) -> float:
        """Mean Reciprocal Rank: 1/rank of first relevant result."""
        relevant_set = {self._normalize_note(n) for n in relevant}
        for i, note in enumerate(retrieved):
            if self._normalize_note(note) in relevant_set:
                return 1.0 / (i + 1)
        return 0.0

    def evaluate_retrieval(
        self,
        query: str,
        retrieved: list[dict],
        relevant: list[str] | None = None,
        k: int = 5,
    ) -> dict:
        """Compute retrieval metrics for a single query.

        Args:
            query: The query string.
            retrieved: List of retrieved result dicts from FUSED retriever.
            relevant: List of note identifiers that should have been retrieved.
                      If None, looks up ground truth from stored mappings.
            k: Number of top results to evaluate.

        Returns:
            Dict with recall@k, precision@k, NDCG@k, MRR, and metadata.
            If no ground truth available, returns metrics as None with
            'ground_truth_available' set to False.
        """
        # Extract note identifiers from retrieved results
        retrieved_notes = []
        for r in retrieved:
            if isinstance(r, dict):
                identifier = r.get("file_path") or r.get("title") or str(r)
                retrieved_notes.append(identifier)
            else:
                retrieved_notes.append(str(r))

        # Get ground truth
        if relevant is None:
            relevant = self._find_ground_truth(query)

        if not relevant:
            return {
                "query": query,
                "k": k,
                "ground_truth_available": False,
                "recall_at_k": None,
                "precision_at_k": None,
                "ndcg_at_k": None,
                "mrr": None,
                "retrieved_count": len(retrieved_notes),
                "relevant_count": 0,
            }

        # Compute metrics
        recall = self._compute_recall_at_k(retrieved_notes, relevant, k)
        precision = self._compute_precision_at_k(retrieved_notes, relevant, k)
        ndcg = self._compute_ndcg_at_k(retrieved_notes, relevant, k)
        mrr = self._compute_mrr(retrieved_notes, relevant)

        result = {
            "query": query,
            "k": k,
            "ground_truth_available": True,
            "recall_at_k": round(recall, 4),
            "precision_at_k": round(precision, 4),
            "ndcg_at_k": round(ndcg, 4),
            "mrr": round(mrr, 4),
            "retrieved_count": len(retrieved_notes),
            "relevant_count": len(relevant),
            "timestamp": datetime.now().isoformat(),
        }

        # Store in metric history
        data = self._load()
        data["metric_history"].append(result)
        self._save(data)

        return result

    # ─── Regression detection ───

    def regression_check(self) -> dict:
        """Compare recent metrics to historical baseline.

        Splits metric history into 'recent' (last N queries) and 'baseline'
        (everything before). If recent metrics drop more than the threshold
        relative to baseline, flags a regression.

        Returns:
            Dict with regression status, recent averages, baseline averages,
            and per-metric regression flags.
        """
        data = self._load()
        history = data.get("metric_history", [])

        if len(history) < self.regression_window * 2:
            return {
                "status": "insufficient_data",
                "message": f"Need {self.regression_window * 2} evaluated "
                f"queries, have {len(history)}",
                "total_evaluated": len(history),
            }

        # Split into baseline and recent
        recent = history[-self.regression_window :]
        baseline = history[: -self.regression_window]

        metrics = ["recall_at_k", "precision_at_k", "ndcg_at_k", "mrr"]
        regressions = {}
        any_regression = False

        for metric in metrics:
            baseline_vals = [h[metric] for h in baseline if h.get(metric) is not None]
            recent_vals = [h[metric] for h in recent if h.get(metric) is not None]

            if not baseline_vals or not recent_vals:
                regressions[metric] = {
                    "status": "no_data",
                    "baseline_avg": None,
                    "recent_avg": None,
                    "drop": None,
                }
                continue

            baseline_avg = sum(baseline_vals) / len(baseline_vals)
            recent_avg = sum(recent_vals) / len(recent_vals)

            if baseline_avg > 0:
                drop = (baseline_avg - recent_avg) / baseline_avg
            else:
                drop = 0.0 if recent_avg == 0 else -1.0

            is_regression = drop > self.regression_threshold
            if is_regression:
                any_regression = True

            regressions[metric] = {
                "status": "regression" if is_regression else "ok",
                "baseline_avg": round(baseline_avg, 4),
                "recent_avg": round(recent_avg, 4),
                "drop_pct": round(drop * 100, 2),
            }

        return {
            "status": "regression_detected" if any_regression else "ok",
            "regressions": regressions,
            "recent_window": len(recent),
            "baseline_window": len(baseline),
            "total_evaluated": len(history),
        }

    # ─── Reporting ───

    def get_metrics_summary(self) -> dict:
        """Get a summary of all metrics over time."""
        data = self._load()
        history = data.get("metric_history", [])

        if not history:
            return {
                "total_queries_logged": len(data.get("retrieval_logs", [])),
                "total_evaluated": 0,
                "ground_truth_count": len(data.get("ground_truth", {})),
                "message": "No metrics computed yet (no ground truth available)",
            }

        metrics = ["recall_at_k", "precision_at_k", "ndcg_at_k", "mrr"]
        summary = {}
        for metric in metrics:
            vals = [h[metric] for h in history if h.get(metric) is not None]
            if vals:
                summary[metric] = {
                    "avg": round(sum(vals) / len(vals), 4),
                    "min": round(min(vals), 4),
                    "max": round(max(vals), 4),
                    "count": len(vals),
                }

        return {
            "total_queries_logged": len(data.get("retrieval_logs", [])),
            "total_evaluated": len(history),
            "ground_truth_count": len(data.get("ground_truth", {})),
            "metrics": summary,
        }

    def get_retrieval_gaps(self) -> list[dict]:
        """Return queries with poor retrieval metrics for the autonomous researcher.

        A query is flagged if:
        - recall@k < 0.7 (missing relevant notes)
        - precision@k < 0.5 (too much noise)
        - MRR < 0.5 (first relevant result not in top 2)

        Returns:
            List of dicts with query, metrics, and gap type.
        """
        data = self._load()
        history = data.get("metric_history", [])
        gaps = []

        for h in history:
            query_gaps = []
            recall = h.get("recall_at_k")
            precision = h.get("precision_at_k")
            mrr = h.get("mrr")

            if recall is not None and recall < 0.7:
                query_gaps.append(f"low_recall ({recall})")
            if precision is not None and precision < 0.5:
                query_gaps.append(f"low_precision ({precision})")
            if mrr is not None and mrr < 0.5:
                query_gaps.append(f"low_mrr ({mrr})")

            if query_gaps:
                gaps.append(
                    {
                        "query": h.get("query"),
                        "gaps": query_gaps,
                        "recall": recall,
                        "precision": precision,
                        "ndcg": h.get("ndcg_at_k"),
                        "mrr": mrr,
                        "timestamp": h.get("timestamp"),
                    }
                )

        return gaps
