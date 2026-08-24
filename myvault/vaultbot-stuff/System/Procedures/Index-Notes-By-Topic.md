---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Scan a directory of vault notes and build a topic index: a mapping of topics to the notes that cover them. The small model classifies each note by topic. Use when building a directory index, when organizing notes by topic, or when creating a table of contents for a directory."
when_to_use: when building a directory index, when organizing notes by topic, when creating a table of contents, or when the vault needs a topic map
falsifiable_if: the topic classifications are wrong, or notes are assigned to topics they don't cover
applies_to:
  - vault-organization
  - topic-indexing
  - vault-maintenance
  - directory-structure
allowed_tools:
  - vault_list
  - llm_generate
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Index-Notes-By-Topic

## When to Run This

Run this when a directory needs a topic index — a map of what topics are
covered and which notes cover them. Useful for building directory-level
index notes.

## Why This Exists

A directory of notes is hard to navigate without a topic map. This procedure
builds a topic index by having the small model classify each note by topic.
The tradeoff: classification is small-model judgment, so topic assignments
may be imperfect.

## Steps

### Step 1: List notes and read previews

1. ```python
import json

directory = args.get("directory", "vaultbot/Knowledge")
vault = Path(vault_path) / directory
if not vault.exists():
    vault = Path(vault_path) / directory

notes = []
for p in vault.rglob("*.md"):
    if "/Procedures/" in str(p) or "/Build-Log/" in str(p):
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            body = text[end+3:]
    notes.append({"stem": p.stem, "path": str(p.relative_to(vault_path)).replace("\\", "/"),
                  "preview": body.strip()[:200]})

result = json.dumps({"notes": notes[:40], "directory": directory})
```

### Step 2: Small model classifies each note by topic

2. ```python
import json as _json

data = _json.loads(output)
notes = data.get("notes", [])
if not notes:
    result = _json.dumps({"index": {}, "note": "no notes found in directory"})
else:
    note_list = "\n".join(f"- {n['stem']}: {n['preview']}" for n in notes)
    prompt = f"""Classify each note by its primary topic. Group notes about
the same topic together. Use concise topic names (1-3 words).

Notes:
{note_list}

Return JSON: {{"topics": [{{"topic": "Topic Name", "notes": ["note1", "note2"], "description": "what this topic covers"}}]}}
Return ONLY the JSON."""
    index = llm_generate(prompt)
    result = index
```

### Step 3: Return the topic index

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"topics": []}
result = _json.dumps({"directory": data.get("directory"),
                      "topics": parsed.get("topics", []),
                      "total_topics": len(parsed.get("topics", []))})
```

## Related

- [[Note-Topic-Classifier]] — finds the single best note for a topic
- [[Note-Similarity-Cluster]] — clusters notes by topic (semantic grouping)
- [[Vault-Topic-Density]] — measures topic coverage density