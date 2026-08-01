"""Tests for the golden-set eval harness (golden_eval.py).

These tests verify the *harness itself* — scoring, normalization, and the
regression gate — using a stub retriever (no FAISS, no Ollama, no network).
They do NOT score the live vault; that requires a built index and is done
separately (manually or in a future integration job). Keeping the harness
tests offline means the gate logic is provable anywhere.

Leaf-module imports only — `import main` is hard-fenced by conftest.py.
"""
from __future__ import annotations

from golden_eval import check_regression, load_golden_set, run_golden_eval


class _StubRetriever:
    """A retriever stub that returns configured results per query substring.

    Maps a substring -> list of result dicts. If no substring matches the
    query, returns an empty result set (simulating a retrieval miss).
    """

    def __init__(self, mapping: dict[str, list[dict]]):
        self._mapping = mapping

    def retrieve(self, query: str, k: int = 10, depth: int = 1):
        q = query.lower()
        for sub, results in self._mapping.items():
            if sub in q:
                return {"results": results[:k], "count": min(len(results), k)}
        return {"results": [], "count": 0}


def test_load_golden_set_reads_seed():
    # The seed golden_set.json ships with the repo; it must load and be
    # non-empty so the gate always has something to check.
    entries = load_golden_set()
    assert len(entries) >= 1
    for e in entries:
        assert e["query"]
        assert e["expected_notes"]


def test_perfect_retrieval_scores_1():
    # A retriever that returns exactly the expected note first should score
    # recall@k = 1.0 and MRR = 1.0 for that query.
    golden = [{"query": "avoid wikipedia", "expected_notes": ["No-Wikipedia-Directive"]}]
    stub = _StubRetriever({
        "wikipedia": [{"file_path": "00-Identity/No-Wikipedia-Directive.md", "score": 0.9}],
    })
    report = run_golden_eval(stub, golden_set=golden, k=5)
    assert report["aggregate"]["recall_at_k"] == 1.0
    assert report["aggregate"]["mrr"] == 1.0
    assert report["per_query"][0]["missing"] == []


def test_miss_scores_0_and_reports_missing():
    # A retriever that returns nothing relevant should score 0 and list the
    # expected note as missing.
    golden = [{"query": "research decision", "expected_notes": ["How-to-Decide-When-to-Research-vs-Answer"]}]
    stub = _StubRetriever({"research": [{"file_path": "unrelated.md", "score": 0.1}]})
    report = run_golden_eval(stub, golden_set=golden, k=5)
    assert report["aggregate"]["recall_at_k"] == 0.0
    assert "How-to-Decide-When-to-Research-vs-Answer" in report["per_query"][0]["missing"]


def test_normalization_matches_paths_case_and_extension():
    # Expected "No-Wikipedia-Directive" should match a retrieved absolute
    # path with different case and a .md extension.
    golden = [{"query": "wiki", "expected_notes": ["no-wikipedia-directive"]}]
    stub = _StubRetriever({
        "wiki": [{"file_path": "C:/Vault/00-Identity/No-Wikipedia-Directive.md", "score": 0.9}],
    })
    report = run_golden_eval(stub, golden_set=golden, k=5)
    assert report["aggregate"]["recall_at_k"] == 1.0


def test_regression_gate_pass_and_fail():
    # Passing report clears the gate; a below-floor recall fails it with a
    # human-readable reason.
    good = {"k": 5, "query_count": 3, "aggregate": {"recall_at_k": 0.8, "ndcg_at_k": 0.7}}
    bad = {"k": 5, "query_count": 3, "aggregate": {"recall_at_k": 0.2, "ndcg_at_k": 0.1}}
    assert check_regression(good, min_recall=0.5)["passed"] is True
    verdict = check_regression(bad, min_recall=0.5)
    assert verdict["passed"] is False
    assert any("recall" in r for r in verdict["reasons"])


def test_empty_golden_set_fails_gate():
    # An empty golden set must NOT silently pass — that's a config error.
    report = run_golden_eval(_StubRetriever({}), golden_set=[], k=5)
    verdict = check_regression(report, min_recall=0.0)
    assert verdict["passed"] is False
    assert any("empty" in r for r in verdict["reasons"])
