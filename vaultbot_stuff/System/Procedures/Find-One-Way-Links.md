---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Find notes that link to something but don't link back — one-way relationships that might indicate the target note should link back. Scans for wikilinks and checks if the target links back. Returns one-way links with a suggestion for whether to add a backlink. Use when strengthening bidirectional connections in the vault graph."
when_to_use: "when strengthening vault graph bidirectionality, when looking for one-way links that should be reciprocal, or during graph organization"
falsifiable_if: "the procedure suggests backlinks that shouldn't be added, or misses one-way links that should be reciprocal"
applies_to:
  - graph-organization
  - wikilinks
  - vault-maintenance
  - connectivity
allowed_tools:
  - vault_list
  - llm_generate
---

# Find-One-Way-Links

## When to Run This

Run this to find one-way wikilinks — note A links to note B, but B doesn't
link back. Some of these should be reciprocal. The small model recommends
which ones to fix.

## Steps

### Step 1: Build the bidirectional link map

1. ```python
import re, json
from collections import defaultdict

all_files = vault_list()
stems = {Path(fp).stem: fp for fp in all_files}
out_links = defaultdict(set)

for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    links = re.findall(r'\[\[([^\]|]+)', text)
    for link in links:
        if link in stems:
            out_links[p.stem].add(link)

# Find one-way links: A→B but not B→A
one_way = []
for source, targets in out_links.items():
    for target in targets:
        if source not in out_links.get(target, set()):
            source_path = str(Path(stems[source]).relative_to(vault_path)).replace("\\", "/")
            target_path = str(Path(stems[target]).relative_to(vault_path)).replace("\\", "/")
            one_way.append({"source": source, "source_path": source_path,
                            "target": target, "target_path": target_path})

result = json.dumps({"one_way_links": one_way[:40], "total": len(one_way)})
```

### Step 2: Small model recommends which to fix

2. ```python
import json as _json

data = _json.loads(output)
links = data.get("one_way_links", [])
if not links:
    result = _json.dumps({"recommendations": [], "note": "no one-way links found"})
else:
    # Sample for the model
    sample = links[:20]
    prompt = f"""For each one-way link (A links to B, but B doesn't link back),
recommend whether to add a backlink from B to A.

One-way links:
{json.dumps(sample, indent=2)}

Return JSON: [{{"source": "A", "target": "B", "add_backlink": true/false, "reason": "why"}}]
Return ONLY the JSON array."""
    recs = llm_generate(prompt)
    result = recs
```

### Step 3: Return the recommendations

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
to_fix = [p for p in parsed if p.get("add_backlink")]
result = _json.dumps({"backlink_suggestions": to_fix,
                      "total_one_way": data.get("total", 0),
                      "suggest_adding": len(to_fix)})
```