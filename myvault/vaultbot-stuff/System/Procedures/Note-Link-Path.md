---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Find the shortest path between two notes in the vault's wikilink graph. Given two note names, traces the wikilink path from one to the other using BFS. Returns the path as a chain of notes. Use when understanding how two concepts connect or when looking for missing links that would shorten the path.
when_to_use: when understanding how two concepts connect, when looking for the relationship between notes, when finding missing links that would improve connectivity, or when asked 'how does X relate to Y'
falsifiable_if: the path doesn't actually exist via wikilinks, or a shorter path exists but wasn't found
applies_to:
  - graph-organization
  - path-finding
  - vault-structure
  - connectivity
allowed_tools:
  - vault_list
  - llm_generate
summary: Note-Link-Path
tags:
  - procedure
  - procedures
---

# Note-Link-Path

## When to Run This

When you want to know how two notes connect through the wikilink graph.
Finds the shortest path and identifies missing links that would shorten it.

## Why This Exists

Understanding how two concepts connect requires tracing the wikilink path
between them. This procedure uses BFS to find the shortest path and suggests
missing links that would shorten it. The tradeoff: it only finds paths via
resolved wikilinks, so unlinked-but-related notes appear disconnected.

## Steps

### Step 1: Build the graph and find the shortest path via BFS

1. ```python
import re, json
from collections import deque, defaultdict

note_a = args.get("note_a", "")
note_b = args.get("note_b", "")
if not note_a or not note_b:
    result = json.dumps({"error": "note_a and note_b arguments required"})
else:
    all_files = vault_list()
    stems = {Path(fp).stem: fp for fp in all_files}
    graph = defaultdict(set)

    for fp in all_files:
        p = Path(fp)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        links = set(re.findall(r'\[\[([^\]|]+)', text))
        for link in links:
            if link in stems:
                graph[p.stem].add(link)

    # BFS from note_a to note_b
    visited = {note_a}
    queue = deque([(note_a, [note_a])])
    path = None
    while queue:
        current, path_so_far = queue.popleft()
        if current == note_b:
            path = path_so_far
            break
        for neighbor in graph.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path_so_far + [neighbor]))

    if path:
        result = json.dumps({"path": path, "path_length": len(path) - 1,
                             "found": True})
    else:
        result = json.dumps({"path": None, "found": False,
                             "note": f"no path from {note_a} to {note_b}"})
```

### Step 2: Small model suggests missing links to shorten the path

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data or not data.get("found"):
    result = data if "error" in data else data
else:
    path = data.get("path", [])
    prompt = f"""This is the shortest wikilink path between two notes:
{path}

Could a direct link between any non-adjacent notes in this path shorten it?
Which notes should link to which to create a shorter path?

Return JSON: {{"suggested_links": [{{"from": "note", "to": "note", "would_shorten_to": N}}], "path_summary": "one sentence describing how the two notes connect"}}
Return ONLY the JSON."""
    analysis = llm_generate(prompt)
    result = analysis
```

### Step 3: Return the path analysis

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"suggested_links": []}
result = _json.dumps({"path": data.get("path"), "path_length": data.get("path_length"),
                      "suggested_shortcuts": parsed.get("suggested_links", []),
                      "path_summary": parsed.get("path_summary", "")})
```

## Related

- [[Note-Dependency-Depth]] — measures load-bearing vs dependent notes
- [[Vault-Graph-Analyzer]] — graph analysis of the vault topology
- [[Note-Linker]] — suggests links to weave the graph tighter