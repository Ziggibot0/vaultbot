---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Score a vault note's quality on 5 dimensions: completeness, accuracy, connectivity, freshness, and clarity. Given a note path, reads the note, checks its links, frontmatter, and content, and the small model scores each dimension 1-5 with reasoning. Use when prioritizing which notes to improve."
when_to_use: when prioritizing which notes to improve, when assessing vault quality, or when asked 'how good is this note'
falsifiable_if: the scores contradict the note's actual quality, or the reasoning is fabricated
applies_to:
  - vault-quality
  - note-assessment
  - vault-maintenance
  - prioritization
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
summary: Note-Quality-Score
tags:
  - procedure
  - procedures
---

# Note-Quality-Score

## When to Run This

Run this when you want to know which notes need work. It scores each note
on 5 dimensions so you can prioritize improvements.

## Why This Exists

With many notes, you need a way to prioritize which to improve. This
procedure scores a note on completeness, accuracy, connectivity, freshness,
and clarity. The tradeoff: the scores are small-model judgment, so they're
relative guidance, not objective measurement.

## Steps

### Step 1: Gather note metrics deterministically

1. ```python
import re, json, os, datetime

note_path = args.get("note_path", "")
if not note_path:
    result = json.dumps({"error": "note_path argument required"})
else:
    p = Path(vault_path) / note_path
    if not p.exists():
        p = Path(note_path)
    if not p.exists():
        result = json.dumps({"error": f"note not found: {note_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Metrics
        has_fm = text.startswith("---")
        links = re.findall(r'\[\[([^\]]+)\]\]', text)
        word_count = len(text.split())
        # Check staleness
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        age_days = (datetime.datetime.now() - mtime).days
        # Check if stub
        is_stub = word_count < 100 or bool(re.search(r'\b(TODO|stub|placeholder|expand)\b', text, re.IGNORECASE))
        # Count backlinks
        all_files = vault_list()
        backlinks = 0
        for fp in all_files:
            if fp == str(p):
                continue
            try:
                t = Path(fp).read_text(encoding="utf-8", errors="replace")
                if f"[[{p.stem}" in t:
                    backlinks += 1
            except Exception:
                continue

        result = json.dumps({
            "note": str(p), "stem": p.stem,
            "has_frontmatter": has_fm, "word_count": word_count,
            "outgoing_links": len(links), "backlinks": backlinks,
            "age_days": age_days, "is_stub": is_stub,
            "first_500": text[:500],
        })
```

### Step 2: Small model scores each dimension

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Score this vault note on 5 dimensions (1-5 each):
1. COMPLETENESS: does it fully cover its topic?
2. ACCURACY: do the claims seem correct and well-sourced?
3. CONNECTIVITY: is it well-linked to related notes?
4. FRESHNESS: is it up to date?
5. CLARITY: is it well-written and easy to follow?

Note: {data['stem']}
Word count: {data['word_count']}
Has frontmatter: {data['has_frontmatter']}
Outgoing links: {data['outgoing_links']}
Backlinks: {data['backlinks']}
Age: {data['age_days']} days
Is stub: {data['is_stub']}
Content preview:
{data['first_500']}

Return JSON: {{"scores": {{"completeness": N, "accuracy": N, "connectivity": N, "freshness": N, "clarity": N}}, "overall": N, "reasoning": "one sentence per dimension", "top_issue": "the biggest problem to fix first"}}
Return ONLY the JSON."""
    scores = llm_generate(prompt)
    result = scores
```

### Step 3: Return the quality score

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"scores": {}, "overall": 0, "error": "could not parse scores"}
result = _json.dumps(parsed)
```

## Related

- [[Note-Consistency-Check]] — checks internal consistency of a note
- [[Note-Accuracy-Check]] — verifies claims against the vault
- [[Find-Thin-Notes]] — finds notes that need expansion