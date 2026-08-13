---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Find notes that are referenced by many other notes but have no incoming links from hub or index notes — notes that should be more prominently linked. Identifies important notes that are discovered but not well-connected to the vault's hub structure. Use when strengthening the vault's organizational structure.
when_to_use: when strengthening vault organization, when important notes aren't connected to hubs, when improving discoverability of key notes, or during graph organization
falsifiable_if: the procedure suggests notes that aren't actually important, or misses notes that should be hub-connected
applies_to:
  - graph-organization
  - hub-structure
  - vault-maintenance
  - discoverability
allowed_tools:
  - vault_list
  - llm_generate
summary: Find-Hub-Notes
tags:
  - procedure
  - procedures
---

# Find-Hub-Notes

## When to Run This

When the vault has important notes that aren't well-connected to its
organizational structure. This finds notes that many others reference but
that aren't linked from any hub or index note.

## Steps

### Step 1: Build the link graph and identify hubs + important-but-unconnected notes

1. ```python
import re, json
from collections import defaultdict

all_files = vault_list()
stems = {Path(fp).stem: fp for fp in all_files}
in_links = defaultdict(list)
out_links = defaultdict(set)

for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    links = re.findall(r'\[\[([^\]|]+)', text)
    for link in links:
        if link in stems:
            in_links[link].append(rel)
            out_links[p.stem].add(link)

# Identify hubs (notes with 8+ incoming links)
hubs = {s for s, links in in_links.items() if len(links) >= 8}
# Identify important notes (5+ incoming links but not linked from any hub)
important_unconnected = []
for stem, linkers in in_links.items():
    if len(linkers) >= 5 and stem not in hubs:
        # Check if any hub links to this note
        linked_by_hub = any(hub in out_links and stem in out_links[hub] for hub in hubs)
        if not linked_by_hub:
            important_unconnected.append({
                "stem": stem, "path": str(Path(stems[stem]).relative_to(vault_path)).replace("\\", "/"),
                "incoming_links": len(linkers),
                "linked_by": linkers[:5],
            })

important_unconnected.sort(key=lambda n: -n["incoming_links"])
result = json.dumps({"hubs": list(hubs)[:10],
                     "important_unconnected": important_unconnected[:15],
                     "total_hubs": len(hubs),
                     "total_unconnected": len(important_unconnected)})
```

### Step 2: Small model recommends which hubs should link to which notes

2. ```python
import json as _json

data = _json.loads(output)
unconnected = data.get("important_unconnected", [])
hubs = data.get("hubs", [])
if not unconnected:
    result = _json.dumps({"suggestions": [], "note": "all important notes are hub-connected"})
else:
    prompt = f"""For each important-but-unconnected note, suggest which hub
note should link to it.

Hubs: {hubs}
Important unconnected notes:
{json.dumps(unconnected[:10], indent=2)}

Return JSON: [{{"note": "stem", "suggested_hub": "hub name", "reason": "why this hub should link to it"}}]
Return ONLY the JSON array."""
    suggestions = llm_generate(prompt)
    result = suggestions
```

### Step 3: Return the hub-linking suggestions

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"hub_link_suggestions": parsed,
                      "total_unconnected": data.get("total_unconnected", 0),
                      "total_hubs": data.get("total_hubs", 0)})
```