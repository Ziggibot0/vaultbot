---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: "Fast vault health snapshot. Runs Pattern-Scan plus the graph analyzer, then formats a concise health report: connectivity, orphan islands, sparse zones, cluster breakdown, and the top bridge suggestions. Small cartridge — the LLM only formats structured graph data into prose, no reasoning over raw notes."
when_to_use: at session start, or whenever asked 'how's the vault?', 'vault status?', or for a graph/connectivity overview
falsifiable_if: the report's connectivity or orphan counts disagree with vault_graph_analyzer or Pattern-Scan output
applies_to:
  - vault-maintenance
  - health
  - graph-organization
allowed_tools:
  - run_procedure
  - vault_graph_analyzer
  - vault_list
summary: Vault-Health-Check
tags:
  - procedure
  - procedures
---

# Vault-Health-Check

## When to Run This

Run at session start or whenever the operator asks how the vault is doing.
Combines [[Pattern-Scan]] (per-note signals) with `vault_graph_analyzer`
(islands/connectivity), then formats a concise health report. The LLM
step only turns structured data into readable prose — cheap to run on the
small cartridge.

## Steps

### Step 1: Gather Pattern-Scan summary + graph analysis

1. ```python
import json

scan = run_procedure("Pattern-Scan")
scan_summary = {}
try:
    scan_summary = json.loads(scan.get("final_output", "{}"))
except Exception:
    pass

graph = vault_graph_analyzer()
analysis = graph.get("analysis", {}) if isinstance(graph, dict) else {}

health = {
    "pattern_counts": scan_summary.get("counts", {}),
    "graph": {
        "num_islands": analysis.get("num_islands"),
        "largest_island_size": analysis.get("largest_island_size"),
        "connectivity_ratio": analysis.get("connectivity_ratio"),
        "num_nodes": analysis.get("num_nodes"),
        "isolated_count": len(analysis.get("isolated_nodes", []) or []),
    },
}
result = json.dumps(health)
```

### Step 2: Format the health report

2. [llm: Format a concise vault health report from the prior step output. Give: (1) a one-line overall grade (Healthy / Needs attention / Fragmenting) based on connectivity_ratio and orphan count, (2) total notes and procedures, (3) connectivity — islands, largest island, isolated count, (4) hygiene — broken links, duplicates, stubs, open tasks, stale notes, (5) the single most impactful thing to do next. Keep it tight — this is a snapshot, not a deep dive; point to Vault-Cleanup for the full to-do list.]

### Step 3: Validate

3. [validate: contains "vault" or contains "health"]
