---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Given a topic, find the single best vault note that explains it. Searches for the topic, reads the top results, and the small model picks the ONE note that best explains it. Returns the note path and a confidence score. Use when you need the definitive note for a topic, not a list of candidates.
when_to_use: when you need the single best note for a topic, when looking for the definitive explanation of something, when you need one authoritative source, or when the first result from vault_search isn't good enough
falsifiable_if: the selected note doesn't actually explain the topic, or a better note exists but wasn't selected
applies_to:
  - vault-search
  - best-match
  - topic-location
  - retrieval
allowed_tools:
  - vault_search
  - llm_generate
summary: Note-Topic-Classifier
tags:
  - procedure
  - procedures
---

# Note-Topic-Classifier

## When to Run This

When you need the ONE best note for a topic. Not a list — the single
definitive note. The small model reads the top search results and picks
the best one.

## Why This Exists

Sometimes you need the single definitive note for a topic, not a list of
candidates. This procedure searches, reads the top results, and picks the
one best note. The tradeoff: it only reads the top 5 search results, so a
better note outside the top 5 won't be selected.

## Steps

### Step 1: Search and read top candidates

1. ```python
import json

topic = args.get("topic", "")
if not topic:
    result = json.dumps({"error": "topic argument required"})
else:
    hits = vault_search(query=topic, k=5)
    candidates = []
    for h in hits:
        fp = h.get("file_path", "")
        if not fp:
            continue
        try:
            text = Path(fp).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(Path(fp).relative_to(vault_path)).replace("\\", "/")
        candidates.append({"path": rel, "stem": Path(fp).stem,
                           "preview": text[:1000], "word_count": len(text.split())})
    result = json.dumps({"topic": topic, "candidates": candidates})
```

### Step 2: Small model picks the best one

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    candidates = data.get("candidates", [])
    if not candidates:
        result = _json.dumps({"best_note": None, "note": "no candidates found"})
    else:
        prompt = f"""Pick the SINGLE best note that explains this topic.
Judge by: completeness, accuracy, clarity, and how directly it addresses the topic.

Topic: {data['topic']}

Candidates:
{json.dumps([{k: v for k, v in c.items() if k != 'preview'} | {'preview': c['preview'][:300]} for c in candidates], indent=2)}

Return JSON: {{"best_note": "path", "stem": "name", "confidence": "high|medium|low", "reason": "why this is the best", "runner_up": "second best path"}}
Return ONLY the JSON."""
    best = llm_generate(prompt)
    result = best
```

### Step 3: Return the best match

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"best_note": None}
result = _json.dumps(parsed)
```

## Related

- [[Index-Notes-By-Topic]] — builds a topic index for a directory
- [[Note-Similarity-Cluster]] — clusters notes by topic
- [[Smart-Vault-Search]] — semantic vault search