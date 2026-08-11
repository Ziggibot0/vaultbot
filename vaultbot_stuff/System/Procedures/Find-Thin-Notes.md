---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Find notes that are too thin — under 200 words, stubs, or placeholders with no real content. Imports the Pattern-Scan engine and filters for thin/stub notes, then the small model suggests what each thin note should be expanded with. Use when looking for notes that need expansion.
when_to_use: when looking for notes that need expansion, when finding stubs and placeholders, when cleaning up thin notes, or when asked 'which notes are incomplete'
falsifiable_if: the procedure flags a note as thin when it's well-developed, or misses notes that are actually thin
applies_to:
  - vault-maintenance
  - thin-notes
  - stubs
  - vault-completeness
allowed_tools:
  - run_procedure
  - llm_generate
provides:
  - Pattern-Scan
summary: Find-Thin-Notes
tags:
  - procedure
  - procedures
---

# Find-Thin-Notes

## When to Run This

Run this to find notes that are too thin to be useful — stubs, placeholders,
or notes with barely any content. The small model then suggests what each
should be expanded with.

## Steps

### Step 1: Import Pattern-Scan and filter for thin notes

1. ```python
import json, os

scan = run_procedure("Pattern-Scan")
out_file = None
try:
    summary = json.loads(scan.get("final_output", "{}"))
    out_file = summary.get("out_file")
except Exception:
    out_file = None
if not out_file or not Path(out_file).exists():
    out_file = str(Path(os.environ.get("VAULT_PATH", ".")) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern-scan-latest.json")

data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

thin = [
    {"path": r["path"], "chars": r["chars"], "is_stub": r.get("is_stub", False),
     "has_frontmatter": r.get("has_frontmatter", False)}
    for r in records if r.get("is_thin") or r.get("is_stub")
]
thin.sort(key=lambda t: t["chars"])

result = json.dumps({"thin_count": len(thin), "thin_notes": thin[:30]})
```

### Step 2: Small model suggests expansion for each thin note

2. ```python
import json as _json

data = _json.loads(output)
thin = data.get("thin_notes", [])
if not thin:
    result = _json.dumps({"suggestions": [], "note": "no thin notes found"})
else:
    # Read the first few thin notes for context
    suggestions = []
    for t in thin[:10]:
        p = Path(vault_path) / t["path"]
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:300]
        except Exception:
            text = ""
        suggestions.append({"path": t["path"], "chars": t["chars"],
                            "content": text})

    prompt = f"""For each thin note, suggest what it should be expanded with.
Base the suggestion on the note name, its current content, and what would
make it useful.

Thin notes:
{_json.dumps(suggestions, indent=2)}

Return JSON: [{{"path": "...", "should_contain": "what to add", "research_needed": true/false}}]
Return ONLY the JSON array."""
    expansion = llm_generate(prompt)
    result = expansion
```

### Step 3: Return the thin note report

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"thin_notes": parsed, "total_thin": data.get("thin_count", 0)})
```