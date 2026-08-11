---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Check if a note is internally consistent — do its claims, frontmatter, and structure agree? Reads a note and checks: does the frontmatter type match the content, do the tags match the topic, do the depends_on links actually relate to the note, and does the body contradict itself? Use when a note feels off or before relying on it."
when_to_use: when a note feels inconsistent, when frontmatter doesn't match content, when tags seem wrong, or before relying on a note that might have internal contradictions
falsifiable_if: the procedure reports an inconsistency that doesn't exist, or misses a real internal contradiction
applies_to:
  - vault-quality
  - consistency-checking
  - vault-maintenance
  - note-validation
allowed_tools:
  - code_read
  - llm_generate
summary: "## SUMMARY|consistency_check_note_analysis_step_1_v3.2025

## TAGS
# frontmatter, body_text, syntax_validation, note_structure, consistency_check"
tags:
  - procedure
  - procedures
---

# Note-Consistency-Check

## When to Run This

Run this when a note feels off — frontmatter says one thing, body says
another, tags don't match the topic. It catches internal contradictions
within a single note.

## Steps

### Step 1: Read the note and extract frontmatter + body

1. ```python
import re, json

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
        fm = {}
        body = text
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm_text = text[3:end]
                body = text[end+3:]
                for line in fm_text.split("\n"):
                    if ":" in line and not line.startswith(" "):
                        key, _, val = line.partition(":")
                        fm[key.strip()] = val.strip().strip('"').strip("'")
        result = json.dumps({"note": str(p), "frontmatter": fm,
                             "body": body[:2000], "body_length": len(body)})
```

### Step 2: Small model checks internal consistency

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    fm = data.get("frontmatter", {})
    body = data.get("body", "")
    prompt = f"""Check this note for internal consistency:

Frontmatter:
{json.dumps(fm, indent=2)}

Body (first 2000 chars):
{body}

Check:
1. Does the frontmatter type match the actual content?
2. Do the tags (if any) match what the note is about?
3. Do the depends_on links (if any) relate to the note's topic?
4. Does the body contradict itself anywhere?
5. Is the description accurate?

Return JSON: {{"consistent": true/false, "issues": [{{"issue": "description", "severity": "high|medium|low", "fix": "suggested fix"}}]}}
Return ONLY the JSON."""
    check = llm_generate(prompt)
    result = check
```

### Step 3: Return the consistency report

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"consistent": True, "issues": []}
result = _json.dumps(parsed)
```