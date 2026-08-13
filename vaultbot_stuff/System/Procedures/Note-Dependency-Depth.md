---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Find notes that are load-bearing — many other notes depend on them — and notes that depend on many others. Scans the wikilink graph, computes in-degree and out-degree for each note, and returns the most depended-on notes (hubs) and the most dependent notes (leaves with many dependencies). Use when understanding vault structure or before deleting a note.
when_to_use: before deleting a note to check if it's load-bearing, when understanding vault structure, when finding hub notes, or when asked 'what notes are most important'
falsifiable_if: the dependency counts are wrong, or the procedure misses important dependencies
applies_to:
  - graph-organization
  - dependency-analysis
  - vault-maintenance
  - vault-structure
allowed_tools:
  - vault_list
  - llm_generate
summary: Note-Dependency-Depth
tags:
  - procedure
  - procedures
---

# Note-Dependency-Depth

## When to Run This

Run this to understand which notes are load-bearing (many notes depend on
them) and which depend on many others. Critical before deleting a note —
you need to know if it's a hub.

## Steps

### Step 1: Compute the dependency graph

1. ```python
import re, json
from collections import defaultdict

all_files = vault_list()
stems = {Path(fp).stem: fp for fp in all_files}
out_degree = defaultdict(int)
in_degree = defaultdict(int)

for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    links = set(re.findall(r'\[\[([^\]|]+)', text))
    resolved = [l for l in links if l in stems]
    out_degree[p.stem] = len(resolved)
    for link in resolved:
        in_degree[link] += 1

# Sort by in-degree (hubs = most depended on)
hubs = sorted([(stem, deg) for stem, deg in in_degree.items()],
              key=lambda x: -x[1])[:20]
# Sort by out-degree (most dependent)
dependent = sorted([(stem, deg) for stem, deg in out_degree.items()],
                   key=lambda x: -x[1])[:20]

# Load-bearing notes (in_degree >= 5)
load_bearing = [{"note": s, "depended_on_by": d} for s, d in hubs if d >= 5]

result = json.dumps({
    "load_bearing_notes": load_bearing,
    "most_dependent_notes": [{"note": s, "depends_on": d} for s, d in dependent],
    "total_notes": len(stems),
})
```

### Step 2: Small model identifies critical notes

2. ```python
import json as _json

data = _json.loads(output)
hubs = data.get("load_bearing_notes", [])
dependent = data.get("most_dependent_notes", [])

prompt = f"""Analyze this vault dependency structure:

Load-bearing notes (many notes depend on them):
{json.dumps(hubs, indent=2)}

Most dependent notes (depend on many others):
{json.dumps(dependent, indent=2)}

Return JSON: {{"critical_notes": ["notes that would cause the most damage if deleted"], "orphan_risks": ["dependent notes that would be harmed if a hub is deleted"], "structural_observations": "one sentence about the vault structure"}}
Return ONLY the JSON."""
    analysis = llm_generate(prompt)
    result = analysis
```

### Step 3: Return the dependency analysis

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"critical_notes": []}
result = _json.dumps({"dependency_map": data, "analysis": parsed})
```