---
type: procedure
status: experimental
model_cartridge: small
created: 2026-07-31
description: "Suggest 1-3 tags for a note based on its content and a neighbor note's content. Returns a JSON array of tag strings. Uses the small model — tagging is simple keyword extraction."
when_to_use: "when a new note is created and you want to suggest tags for a neighboring note (A-MEM evolution)"
falsifiable_if: "the suggested tags are irrelevant to the note's content, or duplicate tags the note already has"
applies_to:
  - a-mem-evolution
  - tagging
  - note-enrichment
allowed_tools:
  - vault_search
  - llm_generate
---

# Suggest-Tags

## When to Run This

Run this procedure when a new note is created and you want to suggest tags for a neighboring note that capture how the new note relates to it. This is the A-MEM evolution step — new notes trigger opportunistic refinement of nearby notes' tags.

## Steps

### Step 1: Gather the new note and neighbor content

1. ```python
new_title = args.get("new_title", "")
neighbor_path = args.get("neighbor_path", "")
if not new_title or not neighbor_path:
    result = json.dumps({"error": "missing new_title or neighbor_path"})
else:
    try:
        neighbor_text = Path(neighbor_path).read_text(encoding="utf-8", errors="replace")[:2000]
    except Exception:
        neighbor_text = ""
    result = json.dumps({"new_title": new_title, "neighbor_preview": neighbor_text})
```

### Step 2: Ask the small model for tag suggestions

2. [llm: Given a new note titled and an existing neighbor note, suggest 1-3 new tags/keywords to add to the neighbor that capture how the new note relates to it. Return ONLY a JSON array of strings, no prose. The new note title and neighbor content are in the prior step output.]

### Step 3: Return the tags

3. ```python
import json
try:
    tags = json.loads(output.strip())
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()][:3]
except Exception:
    tags = []
result = json.dumps({"tags": tags, "count": len(tags)})
```