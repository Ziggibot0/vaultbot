---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Find notes that should be merged because they're fragments of the same topic — each one is too thin alone but together they'd make a complete note. Scans for short notes (< 200 words) that share significant content overlap, and the small model determines which should be merged. Use when cleaning up fragmented knowledge.
when_to_use: when the vault has fragmented notes that should be merged, when short notes overlap significantly, when cleaning up after a period of rapid note-taking, or when asked 'which notes should be combined'
falsifiable_if: the procedure suggests merging notes that shouldn't be merged, or misses merge candidates
applies_to:
  - vault-maintenance
  - note-merging
  - consolidation
  - vault-completeness
allowed_tools:
  - vault_list
  - llm_generate
summary: "SUMMARY|find and merge short fragmented notes from vault logs to improve document structure.
tags#merge-filters,breakpoints,dictionary-terms"
tags:
  - procedure
  - procedures
---

# Note-Merge-Candidates

## When to Run This

When the vault has lots of short, fragmented notes that might be pieces
of the same topic. This finds them and suggests which to merge.

## Steps

### Step 1: Find short notes with overlapping content

1. ```python
import re, json
from collections import defaultdict

all_files = vault_list()
short_notes = []
for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    if "/Procedures/" in rel or "/Build-Log/" in rel or "/Chat/" in rel:
        continue
    word_count = len(text.split())
    if word_count < 200:  # short notes
        # Extract significant words
        words = set(re.findall(r'\b[a-z]{5,}\b', text.lower()))
        short_notes.append({"path": rel, "stem": p.stem,
                            "word_count": word_count,
                            "keywords": words,
                            "preview": text[:300]})

# Find pairs with high keyword overlap
merge_candidates = []
for i in range(len(short_notes)):
    for j in range(i+1, min(i+30, len(short_notes))):
        overlap = short_notes[i]["keywords"] & short_notes[j]["keywords"]
        smaller = min(len(short_notes[i]["keywords"]), len(short_notes[j]["keywords"]))
        if smaller > 0 and len(overlap) / smaller > 0.4 and len(overlap) > 5:
            merge_candidates.append({
                "note_a": short_notes[i]["path"], "note_b": short_notes[j]["path"],
                "stem_a": short_notes[i]["stem"], "stem_b": short_notes[j]["stem"],
                "overlap_ratio": round(len(overlap) / smaller, 2),
                "combined_words": short_notes[i]["word_count"] + short_notes[j]["word_count"],
            })

merge_candidates.sort(key=lambda m: -m["overlap_ratio"])
result = json.dumps({"merge_candidates": merge_candidates[:15],
                     "total_short_notes": len(short_notes)})
```

### Step 2: Small model evaluates which should actually merge

2. ```python
import json as _json

data = _json.loads(output)
candidates = data.get("merge_candidates", [])
if not candidates:
    result = _json.dumps({"merges": [], "note": "no merge candidates found"})
else:
    # Read the candidate notes
    evaluated = []
    for c in candidates[:8]:
        pa = Path(vault_path) / c["note_a"]
        pb = Path(vault_path) / c["note_b"]
        try:
            text_a = pa.read_text(encoding="utf-8", errors="replace")[:400]
            text_b = pb.read_text(encoding="utf-8", errors="replace")[:400]
        except Exception:
            continue
        prompt = f"""Should these two notes be merged?

Note A ({c['stem_a']}, {c['overlap_ratio']*100:.0f}% overlap):
{text_a}

Note B ({c['stem_b']}):
{text_b}

Return JSON: {{"should_merge": true/false, "merged_title": "suggested title if merge", "reason": "why", "which_to_keep": "A or B as the base"}}
Return ONLY the JSON."""
        verdict = llm_generate(prompt)
        try:
            start = verdict.find("{")
            end = verdict.rfind("}")
            parsed = _json.loads(verdict[start:end+1])
            if parsed.get("should_merge"):
                evaluated.append({"note_a": c["note_a"], "note_b": c["note_b"],
                                  **parsed})
        except Exception:
            continue
    result = _json.dumps({"merges": evaluated, "candidates_checked": len(candidates[:8])})
```

### Step 3: Return the merge suggestions

3. ```python
import json as _json

data = _json.loads(output)
merges = data.get("merges", [])
result = _json.dumps({
    "merge_suggestions": merges,
    "total_merges": len(merges),
    "short_notes_found": data.get("total_short_notes", 0),
})
```