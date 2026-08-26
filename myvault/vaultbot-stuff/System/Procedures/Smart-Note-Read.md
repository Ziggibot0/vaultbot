---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Read a vault note and extract its key points, claims, and action items in a structured format. Given a note path, the small model returns a summary, key facts, open questions, and action items. Use when you need to quickly understand what a note says without reading all of it.
when_to_use: when you need to quickly understand a note without reading the whole thing, when summarizing a note for context, when extracting action items from a note, or when asked 'what does this note say'
falsifiable_if: the summary contradicts the note's content, or fabricates points not in the note
applies_to:
  - note-comprehension
  - summarization
  - vault-search
  - context-extraction
allowed_tools:
  - code_read
  - llm_generate
summary: SUMMARY|The note provides a Python script for parsing and extracting structured summaries from markdown notes, offering specific steps for reading and analyzing content efficiently. KEY TOPICS|python,
tags:
  - procedure
  - procedures
---

# Smart-Note-Read

## When to Run This

When you need to understand a note quickly without reading the whole thing.
Returns a structured summary: key points, claims, action items, open questions.

## Why This Exists

Reading a full note to extract its key points wastes context when a structured summary would suffice. This procedure closes that gap by having the small model return a summary, key facts, open questions, and action items. The tradeoff is that it summarizes rather than reads verbatim, so it is only appropriate when a condensed view is enough.

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

### Step 2: Small model extracts structured summary

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Read this note and extract its key information in a structured format.

Note content:
{data['text']}

Return JSON:
{{"summary": "2-3 sentence summary",
  "key_points": ["main point 1", "main point 2"],
  "claims": ["factual claims made"],
  "action_items": ["things to do"],
  "open_questions": ["unanswered questions"],
  "depends_on": ["notes or concepts this note references"]}}
Return ONLY the JSON."""
    summary = llm_generate(prompt)
    result = summary
```

### Step 3: Return the structured summary

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"summary": "could not parse note summary"}
result = _json.dumps(parsed)
```

## Related

- [[Smart-Note-Compare]] — compares two notes' perspectives
- [[Summarize-Conversation]] — the sibling summarization procedure
- [[Condense-Note]] — the sibling condensation procedure