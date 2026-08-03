---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Scan a vault note for step-by-step instructions, checklists, or workflows that are buried in prose, and extract them as candidate procedure specs. The small model identifies actionable sequences and drafts a procedure spec for each. Use when a note describes a how-to that could be proceduralized."
when_to_use: "when a note describes a step-by-step process that could become a procedure, when mining notes for proceduralizable content, or when asked 'can this note be turned into a procedure'"
falsifiable_if: "the extracted procedures are not actually in the note, or the steps are not actionable"
applies_to:
  - procedure-discovery
  - self-improvement
  - note-mining
  - procedure-creation
allowed_tools:
  - code_read
  - llm_generate
---

# Extract-Procedures-From-Note

## When to Run This

Run this when a vault note describes a how-to, checklist, or workflow that
could be proceduralized. The small model extracts the actionable steps and
drafts a procedure spec.

## Steps

### Step 1: Read the note

1. ```python
import json

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
        result = json.dumps({"note": str(p), "text": text[:3000]})
```

### Step 2: Small model extracts procedure candidates

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Scan this note for step-by-step instructions, checklists,
or workflows that could be turned into executable procedures.

Note content:
{data['text']}

For each candidate, draft a procedure spec with:
- Name (tool-style, not tutorial-style)
- Description (specific enough for RAG discovery)
- when_to_use
- model_cartridge (small for classification/extraction, big for reasoning)
- Steps (numbered, with code or LLM steps)

Return JSON: [{{"name": "Procedure-Name", "description": "...", "when_to_use": "...", "cartridge": "small|big", "steps": ["step1", "step2"]}}]
Return ONLY the JSON array."""
    candidates = llm_generate(prompt)
    result = candidates
```

### Step 3: Return the procedure candidates

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"candidates": parsed, "note": data.get("note"),
                      "candidate_count": len(parsed)})
```