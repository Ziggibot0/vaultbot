---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-22
description: "Given a file path or module name, traverse the dependency graph and procedure-code map to predict which other modules and procedures would be affected by a change. Returns a structured JSON impact report."
when_to_use: "Before editing a backend module or procedure, to understand the blast radius. Called by Dev-Cycle before making changes, and by Know-Thyself for self-awareness."
falsifiable_if: The report misses a real dependency, lists a phantom dependency, or returns an invalid JSON structure.
applies_to:
  - self-knowledge
  - change-impact
  - dev-cycle
allowed_tools:
  - code_run
  - code_read
summary: Predicts blast radius of a change to a file or module.
tags:
  - procedure
  - self-knowledge
  - change-impact
---

# Predict-Change-Impact

## Purpose

Given a file path or module name, predict which other modules and procedures
would be affected by a change. This is the "blast radius" calculator for
self-modification: before editing code, know what else might break.

## Why This Exists

The Dev-Cycle needs to assess risk before making changes. If you're about to
edit `chat_handler.py`, you should know that it's imported by 15 other modules
and referenced by 3 procedures. This procedure traverses both the Python
dependency graph and the procedure-code map to give a complete impact report.

## Input

- `args.target` (required): file path or module stem (e.g., `chat_handler` or
  `vaultbot_backend/chat_handler.py`)
- `args.depth` (optional): how many hops to traverse in the dependency graph
  (default: 2). Depth 1 = direct dependents, depth 2 = dependents of
  dependents, etc.

## Output

JSON with:
- `direct_dependents`: modules that directly import the target
- `transitive_dependents`: modules that transitively import the target (up to depth)
- `affected_procedures`: procedures that reference the target or its direct dependents
- `risk_level`: "low" / "medium" / "high" based on count of affected items

## Steps

### Step 1: Traverse dependency graph and procedure map for impact

This step reads the JSON sidecars written by Build-Dependency-Graph and
Map-Procedure-Code (no regex parsing of markdown — the JSON is the
machine-readable contract between the procedures).

```python
import json
import os
from pathlib import Path

vault_root = Path(vault_path)
dep_json_path = vault_root / "vaultbot-stuff" / "Knowledge" / "Architecture" / "Dependency-Graph.json"
proc_json_path = vault_root / "vaultbot-stuff" / "Knowledge" / "Architecture" / "Procedure-Code-Map.json"

target = args.get("target", "")
depth = int(args.get("depth", 2))

# Resolve target to a module stem
target_stem = target
if target_stem.endswith(".py"):
    target_stem = target_stem[:-3]
if "/" in target_stem:
    target_stem = target_stem.rsplit("/", 1)[-1]
if "\\" in target_stem:
    target_stem = target_stem.rsplit("\\", 1)[-1]

# Load JSON sidecars (the contract from Build-Dependency-Graph + Map-Procedure-Code)
reverse_deps = {}
if dep_json_path.exists():
    graph_data = json.loads(dep_json_path.read_text(encoding="utf-8"))
    reverse_deps = {k: set(v) for k, v in graph_data.get("reverse_deps", {}).items()}
else:
    result = json.dumps({
        "error": f"Dependency-Graph.json not found at {dep_json_path}",
        "hint": "Run Build-Dependency-Graph first",
    })
    print(result)

# BFS traversal to find transitive dependents
def find_dependents(start, max_depth):
    visited = {start}
    frontier = {start}
    all_dependents = set()
    for d in range(max_depth):
        next_frontier = set()
        for mod in frontier:
            for dep in reverse_deps.get(mod, set()):
                if dep not in visited:
                    visited.add(dep)
                    next_frontier.add(dep)
                    all_dependents.add(dep)
        frontier = next_frontier
        if not frontier:
            break
    return all_dependents

direct_dependents = reverse_deps.get(target_stem, set())
transitive_dependents = find_dependents(target_stem, depth)

# Load procedure-code map JSON
module_to_procs = {}
if proc_json_path.exists():
    proc_data = json.loads(proc_json_path.read_text(encoding="utf-8"))
    module_to_procs = {k: set(v) for k, v in proc_data.get("module_to_procedures", {}).items()}
else:
    result = json.dumps({
        "error": f"Procedure-Code-Map.json not found at {proc_json_path}",
        "hint": "Run Map-Procedure-Code first",
    })
    print(result)

# Find affected procedures: any procedure that references the target or its direct dependents
affected_procs = set()
for check_stem in [target_stem] + list(direct_dependents):
    for proc in module_to_procs.get(check_stem, set()):
        affected_procs.add(proc)

# Risk level
total_impact = len(direct_dependents) + len(transitive_dependents) + len(affected_procs)
if total_impact == 0:
    risk = "low"
elif total_impact <= 3:
    risk = "medium"
else:
    risk = "high"

result = json.dumps({
    "target": target_stem,
    "depth": depth,
    "direct_dependents": sorted(direct_dependents),
    "transitive_dependents": sorted(transitive_dependents),
    "affected_procedures": sorted(affected_procs),
    "risk_level": risk,
    "total_impact_count": total_impact,
    "note": "Run Build-Dependency-Graph and Map-Procedure-Code first if JSON sidecars are missing.",
}, default=str)

print(result)
```

## Related

- [[Build-Dependency-Graph]] — generates the dependency graph this reads
- [[Map-Procedure-Code]] — generates the procedure-code map this reads
- [[Dev-Cycle]] — the main orchestrator that calls this before editing