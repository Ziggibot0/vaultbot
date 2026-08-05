---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Generate questions a vault note should be able to answer but currently can't. Reads a note, has the small model generate questions about the topic, then checks if the note answers each. Returns unanswered questions as gap-filling prompts. Use when expanding a thin note or finding what's missing.
when_to_use: when expanding a thin note, when finding what a note is missing, when looking for research topics, or when asked 'what should this note cover but doesn't'
falsifiable_if: the questions are trivial or already answered in the note, or the gap detection misses obvious missing content
applies_to:
  - gap-detection
  - note-expansion
  - research-topics
  - vault-completeness
allowed_tools:
  - code_read
  - llm_generate
summary: Note-Question-Gen
tags:
  - procedure
  - procedures
---

# Note-Question-Gen

## When to Run This

Run this when a note feels thin or incomplete. It generates questions the
note *should* answer, checks which ones it doesn't, and returns the gaps
as prompts for research or expansion.

## Steps

### Step 1: Read the note and generate questions

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
        prompt = f"""Generate 8 questions that a reader would expect this note
to answer. Questions should cover the topic thoroughly — what, why, how,
when, trade-offs, alternatives.

Note content:
{text[:2000]}

Return a JSON array of 8 question strings. Return ONLY the JSON array."""
        questions_raw = llm_generate(prompt)
        try:
            start = questions_raw.find("[")
            end = questions_raw.rfind("]")
            questions = json.loads(questions_raw[start:end+1]) if start != -1 else []
        except Exception:
            questions = []
        result = json.dumps({"note": str(p), "questions": questions, "note_text": text[:1500]})
```

### Step 2: Check which questions the note already answers

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    questions = data.get("questions", [])
    note_text = data.get("note_text", "")
    if not questions:
        result = _json.dumps({"unanswered": [], "note": "no questions generated"})
    else:
        prompt = f"""For each question, check if the note already answers it.
Note content:
{note_text}

Questions:
{_json.dumps(questions, indent=2)}

Return JSON: {{"answered": ["question1"], "unanswered": ["question2"], "partial": ["question3 with what's missing"]}}
Return ONLY the JSON."""
        check = llm_generate(prompt)
        result = check
```

### Step 3: Return the gap report

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"unanswered": [], "answered": [], "partial": []}
result = _json.dumps({
    "answered": parsed.get("answered", []),
    "unanswered": parsed.get("unanswered", []),
    "partial": parsed.get("partial", []),
    "gap_count": len(parsed.get("unanswered", [])) + len(parsed.get("partial", [])),
})
```