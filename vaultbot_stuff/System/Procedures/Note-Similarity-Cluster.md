---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Find notes that cover the same topic but use different words. Scans a directory for notes, has the small model classify each by topic, and groups notes that are about the same thing. Also produces a topic-level overview map showing what the vault covers and where knowledge is concentrated or thin. Replaces the former Vault-Topic-Map procedure.
when_to_use: when looking for duplicate or overlapping notes, before consolidating, when the vault feels bloated, when asked 'which notes cover the same thing', or when you need a topic-level map of vault coverage
falsifiable_if: the procedure groups notes that are actually about different things, misses notes that are about the same thing, or the topic map misrepresents vault coverage
applies_to:
  - duplicate-detection
  - topic-clustering
  - vault-maintenance
  - consolidation
  - topic-mapping
allowed_tools:
  - vault_list
  - llm_generate
summary: Note-Similarity-Cluster
tags:
  - procedure
  - procedures
---

# Note-Similarity-Cluster

## When to Run This

Run this when you suspect the vault has multiple notes covering the same
topic with different words. Unlike [[Find-Duplicates]] which catches exact
title duplicates, this catches *semantic* duplicates — notes about the same
thing but named differently.

The output also includes a topic-level overview map showing what topics the
vault covers and where knowledge is concentrated or thin. This absorbs the
former Vault-Topic-Map procedure.

## Steps

### Step 1: Collect note titles and first-paragraph summaries

1. ```python
import json

directory = args.get("directory", "vaultbot_stuff/Knowledge")
vault = Path(vault_path) / directory
if not vault.exists():
    vault = Path(vault_path)

notes = []
for p in vault.rglob("*.md"):
    if "/Procedures/" in str(p) or "/Build-Log/" in str(p):
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    # Get first paragraph after frontmatter
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            body = text[end+3:]
    first_para = body.strip().split("\n\n")[0][:200] if body.strip() else ""
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    notes.append({"path": rel, "name": p.stem, "summary": first_para})

result = json.dumps({"notes": notes, "count": len(notes)})
```

### Step 2: Small model clusters by topic

2. ```python
import json as _json

data = _json.loads(output)
notes = data.get("notes", [])

if not notes:
    result = _json.dumps({"clusters": [], "topic_map": {}, "note": "no notes found"})
else:
    prompt = f"""Group these notes by topic. Notes about the same subject
should be in the same cluster, even if they use different words.
Also produce a topic map showing how many notes cover each topic.

Notes:
{json.dumps(notes, indent=2)}

Return JSON: {{"clusters": [{{"topic": "...", "notes": ["name1", "name2"], "overlap_level": "high|medium|low"}}], "topic_map": {{"topic1": count, "topic2": count}}}}
Return ONLY the JSON."""
    clustered = llm_generate(prompt)
    result = clustered
```

### Step 3: Return clusters and topic map

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"clusters": [], "topic_map": {}}

clusters = parsed.get("clusters", [])
topic_map = parsed.get("topic_map", {})
high_overlap = [c for c in clusters if c.get("overlap_level") == "high"]

result = _json.dumps({"clusters": clusters,
                      "topic_map": topic_map,
                      "high_overlap_count": len(high_overlap),
                      "total_notes": data.get("count", 0)})
```