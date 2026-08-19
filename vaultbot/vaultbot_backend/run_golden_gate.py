"""Live golden-set gate — score the REAL FUSED retriever against golden_set.json.

This is the executable form of the Phase-0 gate. It builds the actual
VaultIndexer + VaultGraph + EmbeddingDrift + FusedRetriever (the same objects
main.py wires up) WITHOUT importing main.py (which would acquire the PID lock
and start the server), then runs ``golden_eval.run_golden_eval`` and prints a
pass/fail verdict.

Run it:
    python run_golden_gate.py                # gate with default floor
    python run_golden_gate.py --min-recall 0.6 --k 5
    python run_golden_gate.py --report out.json   # also write the full report

It needs the real index (vaultbot_index/) + a reachable Ollama for query
embeddings, so it is NOT part of the offline pytest suite — it's the gate you
run before shipping a retrieval-affecting change, and the job CI runs on a box
with the index present. Exit code 0 = pass, 1 = fail, 2 = could not build the
retriever (index/Ollama unavailable).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make leaf modules importable when run as a script from the backend dir.
_BACKEND = Path(__file__).parent.resolve()
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Bypass the PID lock / watcher for any transitive import that might touch
# them (defensive — we only import leaf modules, but keep the env consistent
# with the test harness).
os.environ.setdefault("VAULTBOT_SKIP_LOCK", "1")
os.environ.setdefault("VAULTBOT_SKIP_WATCHER", "1")

from golden_eval import check_regression, load_golden_set, run_golden_eval


def _build_retriever(vault_path: str):
    """Construct the real FusedRetriever exactly as main.py does.

    Returns (retriever, None) on success, or (None, error_message) if the
    index/graph/drift objects can't be built (e.g. Ollama down, no index).
    Imports are deferred so a missing native dep surfaces as a clean error,
    not a traceback at import time.
    """
    try:
        from embedding_drift import EmbeddingDrift
        from fused_retrieval import FusedRetriever
        from vault_graph import VaultGraph
        from vault_indexer import VaultIndexer
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return None, f"import failed: {type(e).__name__}: {e}"

    try:
        indexer = VaultIndexer(vault_path=vault_path)
        indexer.load()  # load the persisted FAISS index from disk
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return None, f"indexer load failed: {type(e).__name__}: {e}"

    try:
        graph = VaultGraph(vault_path=vault_path)
        # VaultGraph.__init__ starts a background daemon thread to build
        # the graph. refresh() returns immediately if the build isn't done
        # (degraded-but-never-blocking design for the live server). But the
        # golden gate needs a FULLY built graph — the graph and backlink
        # channels produce zero candidates on an empty graph, which is the
        # primary cause of low recall on graph-walk and multi-note queries.
        # Wait for the background build to finish before proceeding.
        if hasattr(graph, "_build_thread") and graph._build_thread.is_alive():
            import time

            timeout_s = 120
            start = time.monotonic()
            while (
                graph._build_thread.is_alive() and time.monotonic() - start < timeout_s
            ):
                time.sleep(0.5)
            if graph._build_thread.is_alive():
                print(
                    f"WARNING: VaultGraph background build did not finish "
                    f"within {timeout_s}s — graph channel will be degraded."
                )
        graph.refresh()
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return None, f"graph build failed: {type(e).__name__}: {e}"

    try:
        drift = EmbeddingDrift(state_path=_BACKEND / "embedding_drift.json")
    except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        drift = None  # drift is optional; retrieval works without it

    retriever = FusedRetriever(
        vault_graph=graph, vault_indexer=indexer, embedding_drift=drift
    )

    # Wire the procedure status index so the procedure retrieval boost
    # fires. Without this, procedures (which embed only their description
    # surface) get outranked by full-content notes. The boost is +0.20
    # additive on the normalized score, enough to lift a procedure past
    # a similarly-relevant non-procedure without promoting irrelevant ones.
    # We scan the indexer's metadata for type:procedure frontmatter and
    # build the status index from the frontmatter status field.
    proc_status: dict[str, str] = {}
    import re as _re

    for meta in indexer.metadata:
        fp = meta.get("file_path", "")
        content_preview = meta.get("content_preview", "")
        if not content_preview:
            continue
        if "type: procedure" not in content_preview[:500]:
            continue
        # Extract status (default to empty string = unknown status, still
        # gets the base PROCEDURE_BASE_BOOST).
        status_match = _re.search(r"status:\s*(\S+)", content_preview[:500])
        stem = Path(fp).stem
        proc_status[stem] = status_match.group(1) if status_match else ""
    if proc_status:
        retriever.procedure_status_index = proc_status

    return retriever, None


def _debug_dump(retriever, golden, k):
    """Print top-k results for each query so we can see what's outranking expected notes."""
    from pathlib import Path as _P

    from golden_eval import _norm

    print("=" * 80)
    print("DEBUG: top-k results per query")
    print("=" * 80)
    for entry in golden:
        query = entry["query"]
        expected = entry["expected_notes"]
        exp_norm = {_norm(n) for n in expected}
        try:
            out = retriever.retrieve(query, k=k)
            results = out.get("results", []) if isinstance(out, dict) else []
        except Exception as e:  # noqa: BLE001
            print(f"\n  {query!r}")
            print(f"    ERROR: {e}")
            continue
        print(f"\n  {query!r}")
        print(f"    expected: {expected}")
        for i, r in enumerate(results[:k]):
            name = r.get("name", _P(r.get("file_path", "")).stem)
            score = r.get("score", 0.0)
            channels = r.get("channels", [])
            hit = " <<< HIT" if _norm(name) in exp_norm else ""
            print(
                f"    [{i + 1}] {name:50s} score={score:.4f} channels={channels}{hit}"
            )
        # Check if expected is in the graph
        for exp in expected:
            exp_norm_name = _norm(exp)
            node = retriever.vault_graph.nodes.get(exp_norm_name)
            if node:
                edges = retriever.vault_graph.edges.get(exp_norm_name, set())
                backlinks = retriever.vault_graph.backlinks.get(exp_norm_name, set())
                in_index = any(
                    m.get("file_path") == node.get("file_path")
                    for m in retriever.vault_indexer.metadata
                )
                print(
                    f"    [{exp}] in_graph=Y edges={len(edges)} backlinks={len(backlinks)} in_index={'Y' if in_index else 'N'}"
                )
            else:
                print(f"    [{exp}] in_graph=N *** NOT IN GRAPH ***")
    print("=" * 80)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live golden-set retrieval gate")
    ap.add_argument(
        "--vault",
        default=os.getenv("VAULT_PATH", "."),
        help="Path to the vault root (default: VAULT_PATH or .)",
    )
    ap.add_argument("--k", type=int, default=5, help="retrieval cutoff")
    ap.add_argument(
        "--min-recall",
        type=float,
        default=0.5,
        help="minimum aggregate recall@k to pass",
    )
    ap.add_argument(
        "--min-ndcg", type=float, default=0.0, help="optional minimum aggregate NDCG@k"
    )
    ap.add_argument(
        "--golden",
        default=None,
        help="path to golden_set.json (default: alongside this script)",
    )
    ap.add_argument(
        "--report", default=None, help="write the full JSON report to this path"
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="print top-k results (name, score, channels) for each query",
    )
    args = ap.parse_args()

    golden = load_golden_set(args.golden)
    if not golden:
        print("GATE ERROR: golden set is empty or unreadable.")
        return 2

    retriever, err = _build_retriever(args.vault)
    if retriever is None:
        print(f"GATE ERROR: could not build retriever: {err}")
        return 2

    if args.debug:
        _debug_dump(retriever, golden, args.k)

    report = run_golden_eval(retriever, golden_set=golden, k=args.k)
    verdict = check_regression(
        report, min_recall=args.min_recall, min_ndcg=args.min_ndcg
    )

    agg = report["aggregate"]
    print(f"Golden-set gate: {report['query_count']} queries, k={args.k}")
    print(
        f"  recall@{args.k}   = {agg['recall_at_k']:.3f}  (floor {args.min_recall:.3f})"
    )
    print(f"  precision@{args.k}= {agg['precision_at_k']:.3f}")
    print(f"  ndcg@{args.k}     = {agg['ndcg_at_k']:.3f}")
    print(f"  mrr          = {agg['mrr']:.3f}")
    # Surface the actionable failures: which queries missed which notes.
    misses = [pq for pq in report["per_query"] if pq["missing"]]
    if misses:
        print("  misses:")
        for pq in misses:
            print(f"    - {pq['query']!r} missing {pq['missing']}")

    if args.report:
        try:
            Path(args.report).write_text(
                json.dumps({"report": report, "verdict": verdict}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            print(f"  (could not write report: {e})")

    if verdict["passed"]:
        print("GATE: PASS")
        return 0
    print("GATE: FAIL — " + "; ".join(verdict["reasons"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
