---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Suggest tags for a note based on its content. Reads the note, extracts topics and themes, and the small model suggests 3-5 tags that match what the note is about. Checks existing tags in the vault for consistency. Use when tagging a new note or improving an untagged note's discoverability.
when_to_use: when tagging a new note, when a note has no tags, when improving note discoverability, or when asked 'what tags should this note have'
falsifiable_if: the suggested tags don't match the note's content, or are too generic to be useful
applies_to:
  - tagging
  - note-improvement
  - vault-organization
  - discoverability
allowed_tools:
  - vault_list
  - llm_generate
summary: Note-Tags-From-Content
tags:
  - procedure
  - procedures
---

# Note-Tags-From-Content

## When to Run This

When a note has no tags or could use better ones. The small model reads
the content and suggests tags that match what the note is about, checking
existing vault tags for consistency.

## Steps

### Step 1: Read the note and collect existing tags in the vault

1. ```python
import re, json
from collections import Counter

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
        # Collect existing tags from the vault
        all_tags = Counter()
        for fp in vault_list():
            try:
                t = Path(fp).read_text(encoding="utf-8", errors="replace")
                # Find tags in frontmatter
                if t.startswith("---"):
                    end = t.find("---", 3)
                    if end != -1:
                        fm = t[3:end]
                        for m in re.finditer(r'tags:\s*\n((?:\s+-\s+.*\n)*)', fm):
                            for tag in re.findall(r'-\s+(\w+)', m.group(1)):
                                all_tags[tag] += 1
                        # Also inline tags
                        for m in re.finditer(r'tags:\s*\[(.*?)\]', fm):
                            for tag in m.group(1).split(","):
                                tag = tag.strip().strip('"').strip("'")
                                if tag:
                                    all_tags[tag] += 1
            except Exception:
                continue
        result = json.dumps({"note": str(p), "text": text[:2000],
                             "existing_tags": all_tags.most_common(30)})
```

### Step 2: Small model suggests tags

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    text = data.get("text", "")
    existing = data.get("existing_tags", [])
    existing_list = [t[0] for t in existing]
    prompt = f"""Suggest 3-5 tags for this note. Use existing vault tags
when they fit, and create new ones only if needed.

Note content:
{text[:1500]}

Existing vault tags: {existing_list}

Return JSON: {{"tags": ["tag1", "tag2"], "new_tags": ["tags not in existing list"], "reasoning": "one sentence"}}
Return ONLY the JSON."""
    tags = llm_generate(prompt)
    result = tags
```

### Step 3: Return the tag suggestions

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"tags": []}
result = _json.dumps(parsed)
```