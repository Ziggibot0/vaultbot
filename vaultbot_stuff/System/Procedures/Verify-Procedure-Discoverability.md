---
type: procedure
status: active
model_cartridge: small
created: 2026-08-05
description: "Verify that a procedure is discoverable by RAG retrieval — i.e. that when a user says something relevant to the procedure's intent, the fused retriever surfaces it in the top results. Runs a set of test queries (from the procedure's when_to_use phrasings) through the FusedRetriever and reports whether the target procedure ranks #1. Use when a procedure exists but the vaultbot doesn't seem to reach for it when it should, when tuning frontmatter for discoverability, or after creating a new procedure to confirm it will be found."
when_to_use: when a procedure isn't being used when it should be, when you suspect RAG retrieval isn't surfacing a procedure, when you want to verify a new procedure is discoverable, when tuning a procedure's description/when_to_use for better retrieval, after creating or editing a procedure
falsifiable_if: it reports a procedure as discoverable when the retriever doesn't actually return it in the top-5 for any test query (verifiable by running the retriever directly)
applies_to:
  - procedure-discoverability
  - rag-retrieval
  - troubleshooting
  - self-diagnosis
  - procedure-maintenance
allowed_tools:
  - code_run
summary: Verification Procedure Discoverability in RAG Context; Verified against FusedRetriever with `when_to_use` phrasings, validates procedures not reachable by VaultBot via test queries.
tags:
  - procedure
  - procedures
  - troubleshooting
  - rag
  - discoverability
---

# Verify-Procedure-Discoverability

Checks whether a procedure actually surfaces in RAG results when a user
types something relevant to its intent. The procedure's `when_to_use`
phrasings are used as test queries, and the FusedRetriever is run for
each to see if the target procedure ranks #1.

Read-only — runs retrieval queries, never modifies anything.

## When to Run This

- A procedure exists but the vaultbot doesn't reach for it when it should
- After creating a new procedure, to confirm it will be found
- After tuning a procedure's `description`/`when_to_use` frontmatter
- Diagnosing "why doesn't the bot use procedure X?"

## Inputs

- `procedure_name` (required): the stem of the procedure to verify (e.g. `Analyze-Session-Log`)
- `queries` (optional): a list of test query strings. If not provided,
  the procedure's `when_to_use` field is split into phrasings and used.

## Steps

1. ```python
   import json, re, pathlib, sys
   from pathlib import Path

   proc_name = (args.get("procedure_name") or "").strip()
   custom_queries = args.get("queries") or []

   if not proc_name:
       result = "ERROR: procedure_name is required"
   else:
       proc_dir = Path(vault_path) / "vaultbot_stuff" / "System" / "Procedures"
       proc_file = proc_dir / f"{proc_name}.md"
       if not proc_file.exists():
           matches = list(proc_dir.glob(f"{proc_name}*.md"))
           proc_file = matches[0] if matches else None

       if proc_file is None or not proc_file.exists():
           result = f"ERROR: procedure not found: {proc_name}"
       else:
           text = proc_file.read_text(encoding="utf-8", errors="replace")
           fm = {}
           if text.startswith("---"):
               end = text.find("---", 3)
               if end != -1:
                   for line in text[3:end].split("\n"):
                       if ":" in line and not line.startswith("  "):
                           key, _, val = line.partition(":")
                           fm[key.strip()] = val.strip().strip('"').strip("'")

           when_to_use = fm.get("when_to_use", "")
           test_queries = list(custom_queries)
           if not test_queries and when_to_use:
               quoted = re.findall(r'["\']([^"\']+)["\']', when_to_use)
               test_queries.extend(quoted[:10])
               if not quoted:
                   parts = [p.strip() for p in when_to_use.split(",")]
                   test_queries.extend([p for p in parts if len(p) > 5][:5])
           if not test_queries:
               test_queries = [fm.get("description", proc_name)[:80]]

           # Run the FusedRetriever
           backend = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend"
           if str(backend) not in sys.path:
               sys.path.insert(0, str(backend))
           from vault_graph import VaultGraph
           from vault_indexer import VaultIndexer
           from fused_retrieval import FusedRetriever
           from procedure_tracker import ProcedureTracker

           graph = VaultGraph(str(vault_path))
           indexer = VaultIndexer(str(vault_path))
           indexer.load()
           log_p = backend / "procedure_tracker.log"
           tracker = ProcedureTracker(str(log_p), str(vault_path))
           proc_idx = tracker.get_procedure_index(str(vault_path))
           status_map = {s: e.get("frontmatter", {}).get("status", "")
                         for s, e in proc_idx.items()}
           retriever = FusedRetriever(graph, indexer)
           retriever.procedure_status_index = status_map

           target = proc_name.lower()
           hits = 0
           rank1 = 0
           details = []
           for q in test_queries:
               r = retriever.retrieve(q, k=5)
               results = r.get("results", [])
               found = None
               top5 = []
               for i, m in enumerate(results[:5]):
                   stem = Path(m.get("file_path", "")).stem.lower()
                   top5.append(stem)
                   if stem == target:
                       found = i + 1
               if found is not None:
                   hits += 1
                   if found == 1:
                       rank1 += 1
               details.append({"query": q, "found_rank": found, "top5": top5})

           lines = []
           lines.append(f"PROCEDURE: {proc_name}")
           lines.append(f"Queries run: {len(test_queries)}")
           lines.append(f"In top-5: {hits}/{len(test_queries)}")
           lines.append(f"Ranked #1: {rank1}/{len(test_queries)}")
           lines.append(f"Discoverable: {'YES' if hits > 0 else 'NO'}")
           lines.append(f"Fully discoverable: {'YES' if hits == len(test_queries) else 'NO'}")
           lines.append("")
           for d in details:
               rank_str = f"#{d['found_rank']}" if d['found_rank'] else "NOT FOUND"
               lines.append(f"  [{rank_str}] {d['query']!r}")
               lines.append(f"       top5: {', '.join(d['top5'][:3])}")
           result = "\n".join(lines)
   ```