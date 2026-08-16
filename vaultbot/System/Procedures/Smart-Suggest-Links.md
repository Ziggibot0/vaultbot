---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Suggest wikilinks to add to a note based on what it's about. Reads the note content, identifies concepts that have existing notes, and suggests [[wikilinks]] to add. More precise than a simple keyword scan because it reads context and only suggests links that make sense. Use after writing a note or during graph organization."
when_to_use: after writing a new note, when improving a note's connectivity, during graph organization, or when asked 'what should this note link to'
falsifiable_if: the procedure suggests links that don't make sense, or misses obvious link opportunities
applies_to:
  - graph-organization
  - wikilinks
  - vault-maintenance
  - note-improvement
allowed_tools:
  - vault_list
  - llm_generate
summary: Smart-Suggest-Links reads notes to suggest wikilinks by identifying concepts with existing links and filtering for meaningful stems.
tags:
  - procedure
  - procedures
---

# Smart-Suggest-Links

## When to Run This

After writing a note, run this to get link suggestions. It reads the note,
finds concepts that have existing notes, and suggests which to wikilink.
More context-aware than a simple keyword scan — it reads the note's actual meaning before suggesting links.

## Steps

### Step 1: Read the note and list all note stems

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
        # Get all existing note stems
        all_stems = sorted([Path(fp).stem for fp in vault_list()])
        # Filter to stems that are reasonable link targets (>4 chars)
        linkable = [s for s in all_stems if len(s) > 4]
        # Find already-linked concepts
        already_linked = set(re.findall(r'\[\[([^\]|]+)', text))
        result = json.dumps({"note": str(p), "text": text[:2000],
                             "linkable_stems": linkable[:100],
                             "already_linked": list(already_linked)})
```

### Step 2: Small model suggests links based on context

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    text = data.get("text", "")
    linkable = data.get("linkable_stems", [])
    already = data.get("already_linked", [])
    # Filter out already linked
    candidates = [s for s in linkable if s not in already]

    prompt = f"""Read this note and suggest which existing notes should be
wikilinked from it. Only suggest links that make sense in context.

Note content:
{text}

Already linked: {already}
Candidate notes to link: {candidates[:80]}

Return JSON: [{{"target": "Note-Stem", "context": "where in the note this link should go", "reason": "why this link makes sense"}}]
Return ONLY the JSON array."""
    suggestions = llm_generate(prompt)
    result = suggestions
```

### Step 3: Return the link suggestions

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"link_suggestions": parsed, "note": data.get("note"),
                      "suggestion_count": len(parsed)})
```