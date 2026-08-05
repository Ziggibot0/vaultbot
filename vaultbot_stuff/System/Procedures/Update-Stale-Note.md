---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Given a note that describes a system behavior, check if that behavior still matches the code and update the note if it doesn't. Reads the note, reads the referenced code, and the small model produces an updated version of the note's description section. Does NOT write to disk — returns the updated text for the big model to review and write.
when_to_use: when a note is outdated and needs updating, after a code change that affects documented behavior, when fixing a stale note, or when asked to 'update this note to match the code'
falsifiable_if: the updated note still doesn't match the code, or the update introduces incorrect information
applies_to:
  - vault-code-sync
  - note-updating
  - documentation-accuracy
  - vault-maintenance
allowed_tools:
  - code_read
  - llm_generate
summary: Update-Stale-Note
tags:
  - procedure
  - procedures
---

# Update-Stale-Note

## When to Run This

When you know a note is stale and want an updated version. The small model
reads the note and the current code, then produces an updated description
section. The big model reviews and writes it with `vault_safe_write`.

## Steps

### Step 1: Read the note and the referenced code

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
        note_text = p.read_text(encoding="utf-8", errors="replace")
        # Find referenced .py files
        py_refs = list(set(re.findall(r'[\w_/]+\.py', note_text)))
        backend_dir = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend"
        code_texts = []
        for pyf in py_refs[:3]:
            cp = backend_dir / pyf
            if not cp.exists():
                matches = list(backend_dir.rglob(Path(pyf).name))
                cp = matches[0] if matches else None
            if cp and cp.exists():
                try:
                    code_texts.append({"file": str(cp.relative_to(backend_dir)),
                                       "content": cp.read_text(encoding="utf-8", errors="replace")[:1500]})
                except Exception:
                    continue
        result = json.dumps({"note_path": str(p), "note_text": note_text[:2000],
                             "code_refs": code_texts})
```

### Step 2: Small model produces the updated note section

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""This note describes system behavior. Check if it's still
accurate against the code, and produce an UPDATED version of the note's
description sections.

Note:
{data['note_text']}

Current code:
{_json.dumps(data.get('code_refs', []), indent=2)}

Rules:
- Only update sections that are outdated
- Keep the note's structure and style
- Don't change historical sections or records
- Mark updated sections with <!-- updated YYYY-MM-DD -->

Return JSON: {{"updated": true/false, "updated_text": "the full updated note text", "changes": ["list of what was changed"]}}
Return ONLY the JSON."""
    updated = llm_generate(prompt)
    result = updated
```

### Step 3: Return the updated note

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"updated": False, "updated_text": "", "changes": []}
result = _json.dumps(parsed)
```