---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Find notes that cover a specific topic but are written from different angles or for different audiences. Given a topic, finds all notes about it and the small model classifies each by perspective (overview, how-to, reference, analysis, record). Can also diff two specific notes about the same concept to highlight what each covers that the other doesn't. Replaces the former Note-Content-Diff procedure.
when_to_use: when understanding the different perspectives in the vault, before consolidating notes on the same topic, when classifying notes by their role, when asked 'what kinds of notes do I have about X', or when comparing two notes covering the same concept
falsifiable_if: the perspective classifications are wrong, notes are misclassified, or the diff misses real content differences
applies_to:
  - vault-organization
  - note-classification
  - consolidation
  - vault-maintenance
  - content-diff
allowed_tools:
  - vault_list
  - llm_generate
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Smart-Note-Compare

## When to Run This

When you have multiple notes about the same topic and want to understand
how they differ in perspective. One might be an overview, another a
how-to, another a reference. Useful before consolidating.

Can also be used to diff two specific notes about the same concept,
highlighting what each covers that the other doesn't. This absorbs the
former Note-Content-Diff procedure.

## Why This Exists

Multiple notes about the same topic can be written from different angles, and consolidating them without understanding those differences risks losing coverage. This procedure closes that gap by classifying each note by perspective, or diffing two specific notes to highlight what each covers that the other doesn't. The tradeoff is that it absorbs the former Note-Content-Diff procedure, so one procedure now handles both perspective classification and content diffing.

## Arguments

- `topic` (string): The topic to find and classify notes about. Required for topic mode.
- `note_a` (string): First note path. Required for diff mode.
- `note_b` (string): Second note path. Required for diff mode.

## Steps

### Step 1: Find notes about the topic OR load two specific notes

1. ```python
import json

note_a = args.get("note_a", "")
note_b = args.get("note_b", "")

if note_a and note_b:
    # Diff mode: load two specific notes
    notes = []
    for label, path in [("A", note_a), ("B", note_b)]:
        try:
            p = Path(path)
            if not p.exists():
                p = Path(vault_path) / path
            text = p.read_text(encoding="utf-8", errors="replace")
            notes.append({"label": label, "path": path, "text": text[:2000]})
        except Exception:
            notes.append({"label": label, "path": path, "text": "[unreadable]"})
    result = json.dumps({"mode": "diff", "notes": notes})
else:
    # Topic mode: find all notes about the topic
    topic = args.get("topic", "")
    if not topic:
        result = json.dumps({"error": "topic or (note_a + note_b) required"})
    else:
        all_files = vault_list()
        matches = []
        topic_lower = topic.lower()
        for fp in all_files:
            p = Path(fp)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if topic_lower in text.lower() or topic_lower in p.stem.lower():
                rel = str(p.relative_to(vault_path)).replace("\\", "/")
                # Get first paragraph
                body = text
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end != -1:
                        body = text[end+3:]
                first_para = body.strip().split("\n\n")[0][:300] if body.strip() else ""
                matches.append({"path": rel, "name": p.stem, "preview": first_para})
        result = json.dumps({"mode": "topic", "matches": matches, "topic": topic})
```

### Step 2: Small model classifies perspectives or diffs content

2. ```python
import json as _json

data = _json.loads(output)
mode = data.get("mode", "")

if mode == "diff":
    notes = data.get("notes", [])
    prompt = f"""Compare these two notes and identify what each covers that the other doesn't.

Note A:
{notes[0]['text'] if len(notes) > 0 else ''}

Note B:
{notes[1]['text'] if len(notes) > 1 else ''}

Return JSON: {{"only_in_a": ["fact1", ...], "only_in_b": ["fact1", ...], "shared": ["fact1", ...], "recommendation": "merge/keep_separate/expand_a/expand_b"}}
Return ONLY the JSON."""
    diff = llm_generate(prompt)
    result = diff
else:
    matches = data.get("matches", [])
    topic = data.get("topic", "")
    if not matches:
        result = _json.dumps({"perspectives": [], "note": f"no notes found about '{topic}'"})
    else:
        prompt = f"""Classify each note by its perspective on the topic '{topic}'.

Notes:
{json.dumps(matches, indent=2)}

For each, assign a perspective: overview, how-to, reference, analysis, record, or other.
Return JSON: [{{"path": "...", "name": "...", "perspective": "...", "reason": "why"}}]
Return ONLY the JSON array."""
    classified = llm_generate(prompt)
    result = classified
```

### Step 3: Return the results

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    if start == -1:
        start = output.find("{")
        end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {}

if isinstance(parsed, list):
    by_persp = {}
    for p in parsed:
        persp = p.get("perspective", "unknown")
        by_persp.setdefault(persp, []).append(p.get("name", ""))
    result = _json.dumps({"perspectives": by_persp, "total": len(parsed)})
else:
    result = _json.dumps(parsed)
```

## Related

- [[Note-Merge-Candidates]] — identifies notes to merge after comparison
- [[Find-Duplicates]] — finds near-duplicate notes to compare
- [[Smart-Note-Read]] — reads a single note's key points