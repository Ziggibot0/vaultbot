---
type: procedure
status: active
model_cartridge: small
created: 2026-08-05
description: "Diagnose and fix the general class of problem where a vault note or procedure is in the vault but doesn't surface in RAG results when a user says something relevant to it. Runs the retriever with a test query, inspects whether the target note appears in the FAISS index, the vector top-k, the fused merged pool, and whether the procedure-boost rerank is being applied. Reports the root cause: (a) note not in FAISS index, (b) note in index but below the k cutoff, (c) note in merged pool but outranked by non-procedure notes, or (d) procedure boost not applied due to name-resolution mismatch. Use when a note 'should be found' but isn't, when retrieval results seem wrong, or when a procedure isn't being used despite existing."
when_to_use: "when a note or procedure should be surfaced by RAG but isn't, when retrieval results seem wrong or incomplete, when a procedure exists but the vaultbot doesn't use it, when diagnosing why a specific note isn't being found, after changing the fused retriever or indexer and needing to verify retrieval still works"
falsifiable_if: "it diagnoses a root cause that doesn't match the actual retrieval behavior (verifiable by running the retriever directly with the same query)"
applies_to:
  - rag-retrieval
  - troubleshooting
  - self-diagnosis
  - retrieval-debugging
  - procedure-maintenance
allowed_tools:
  - code_run
summary: Diagnose-Retrieval-Failure
tags:
  - procedure
  - procedures
  - troubleshooting
  - rag
  - retrieval
---

# Diagnose-Retrieval-Failure

Diagnoses the general class of "a note should be found by RAG but
isn't" problems. The procedure walks the retrieval pipeline stage by
stage to pinpoint WHERE the target note falls out: not indexed → not in
vector hits → not in merged pool → outranked in rerank → boost not
applied.

Read-only — runs retrieval queries, never modifies anything.

## When to Run This

- A note/procedure should be found by RAG but isn't
- Retrieval results seem wrong or incomplete
- A procedure exists but the vaultbot doesn't use it
- After changing the fused retriever or indexer, to verify retrieval

## Inputs

- `query` (required): a natural-language query that SHOULD surface the
  target note (e.g. "what went wrong in the last session")
- `target` (required): the file stem of the note that SHOULD be found
  (e.g. `Analyze-Session-Log`)

## Steps

1. ```python
   import json, pathlib, sys
   from pathlib import Path

   query = (args.get("query") or "").strip()
   target = (args.get("target") or "").strip().lower()

   if not query or not target:
       result = "ERROR: both 'query' and 'target' are required"
   else:
       backend = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend"
       if str(backend) not in sys.path:
           sys.path.insert(0, str(backend))
       from vault_indexer import VaultIndexer
       from vault_graph import VaultGraph
       from fused_retrieval import FusedRetriever
       from procedure_tracker import ProcedureTracker

       indexer = VaultIndexer(str(vault_path))
       indexer.load()

       # Stage 1: In FAISS index?
       in_index = False
       index_rank = None
       index_score = None
       raw = indexer.search(query, k=50)
       for i, r in enumerate(raw):
           stem = Path(r.get("file_path", "")).stem.lower()
           if stem == target:
               in_index = True
               index_rank = i + 1
               index_score = r.get("score", 0)
               break

       # Stage 2: In top-10 / top-5?
       in_top10 = in_index and index_rank <= 10
       in_top5 = in_index and index_rank <= 5

       # Stage 3: In fused results?
       graph = VaultGraph(str(vault_path))
       log_p = backend / "procedure_tracker.log"
       tracker = ProcedureTracker(str(log_p), str(vault_path))
       proc_idx = tracker.get_procedure_index(str(vault_path))
       status_map = {s: e.get("frontmatter", {}).get("status", "")
                     for s, e in proc_idx.items()}
       retriever = FusedRetriever(graph, indexer)
       retriever.procedure_status_index = status_map
       fused_r = retriever.retrieve(query, k=5)
       fused_results = fused_r.get("results", [])
       fused_rank = None
       fused_score = None
       for i, m in enumerate(fused_results):
           stem = Path(m.get("file_path", "")).stem.lower()
           if stem == target:
               fused_rank = i + 1
               fused_score = m.get("score", 0)
               break

       # Stage 4: Is it a procedure, boost applied?
       is_procedure = target in {s.lower() for s in status_map}
       proc_status = None
       boost_applied = False
       if is_procedure:
           for s, st in status_map.items():
               if s.lower() == target:
                   proc_status = st
                   break
           lookup = retriever._procedure_status_lookup(target)
           boost_applied = lookup is not None

       # Root cause
       if not in_index:
           rc = "NOT IN FAISS INDEX — the note hasn't been indexed. Run the indexer or wait for the watcher to pick it up."
       elif not in_top10:
           rc = f"IN INDEX but rank {index_rank} in FAISS (below top-10) — the note's embedding is too distant from the query. Tune the note's description/frontmatter to match the query language."
       elif fused_rank is None:
           rc = "IN FAISS TOP-10 but NOT in fused results — the merged pool or rerank dropped it. Check MIN_SCORE_THRESHOLD or graph/backlink channel boosts."
       elif is_procedure and not boost_applied:
           rc = "PROCEDURE BOOST NOT APPLIED — name-resolution mismatch between procedure_status_index keys and merged pool normalized names."
       elif fused_rank > 1:
           rc = f"IN FUSED RESULTS at rank {fused_rank} but not #1 — other notes score higher."
       else:
           rc = "NO ISSUE FOUND — the target is ranking #1 in fused results."

       fused_top5 = [Path(m.get("file_path", "")).stem for m in fused_results[:5]]
       lines = []
       lines.append(f"DIAGNOSIS for target={target!r} query={query!r}")
       lines.append("")
       lines.append(f"Stage 1 — In FAISS index: {'YES' if in_index else 'NO'}" +
                    (f" (rank {index_rank}, score {index_score:.4f})" if in_index else ""))
       lines.append(f"Stage 2 — In FAISS top-10: {'YES' if in_top10 else 'NO'}")
       lines.append(f"Stage 3 — In fused results: {'YES' if fused_rank else 'NO'}" +
                    (f" (rank {fused_rank}, score {fused_score:.4f})" if fused_rank else ""))
       lines.append(f"Stage 4 — Is procedure: {'YES' if is_procedure else 'NO'}" +
                    (f" (status={proc_status}, boost={boost_applied})" if is_procedure else ""))
       lines.append("")
       lines.append(f"FUSED TOP-5: {', '.join(fused_top5)}")
       lines.append("")
       lines.append(f"ROOT CAUSE: {rc}")
       result = "\n".join(lines)
   ```