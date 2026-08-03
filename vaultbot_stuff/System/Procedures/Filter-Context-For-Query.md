---
type: procedure
status: verified
model_cartridge: small
created: 2026-08-02
description: "Filter vault context sections by relevance to a query. Given a user query and a list of context sections (each with an id, title, and preview), the small model picks which sections to keep and which to drop. Returns a JSON array of section IDs to keep. Used by the chat pipeline to reduce what the big model sees."
when_to_use: "when the vault context is large and needs filtering before the big model processes it"
falsifiable_if: "the filter drops sections that are relevant to the query, or keeps sections that are irrelevant"
applies_to:
  - context-filtering
  - rag
  - token-efficiency
allowed_tools:
  - llm_generate
success_count: 8
failure_count: 0
success_rate: 1.0
---

# Filter-Context-For-Query

## When to Run This

Called by the chat pipeline after `build_abstract_context` produces a
context string. The small model reads section titles + previews and picks
which sections the big model actually needs. This cuts big-model input
token cost.

## Steps

### Step 1: Format sections as a numbered list

1. ```python
import json

query = args.get("query", "")
sections = args.get("sections", [])

if not query or not sections:
    result = json.dumps({"error": "query and sections required",
                         "keep_ids": list(range(len(sections)))})
else:
    lines = []
    for s in sections:
        sid = s.get("id", 0)
        title = s.get("title", "")
        preview = s.get("preview", "")[:200]
        lines.append(f"[{sid}] {title}\n  {preview}")
    result = json.dumps({"query": query,
                         "formatted": "\n\n".join(lines),
                         "count": len(sections)})
```

### Step 2: Small model picks which sections to keep

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""You are filtering vault context for a user query.
Below are {data['count']} context sections. Decide which are RELEVANT
to the query and should be kept. Return a JSON array of section IDs to KEEP.
Drop sections that are clearly unrelated to the query.

Query: {data['query']}

Sections:
{data['formatted']}

Return ONLY a JSON array of integers, e.g. [0, 2, 5]"""
    response = llm_generate(prompt)
    result = response
```

### Step 3: Parse and return the kept section IDs

3. ```python
import json as _json

try:
    start = output.find("[")
    end = output.rfind("]")
    keep_ids = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    # Fallback: keep all sections (fail-safe).
    data = _json.loads(args.get("_sections_json", "[]"))
    keep_ids = list(range(len(data)))

result = _json.dumps({"keep_ids": keep_ids})
```