---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Diff a single vault note against the actual code it describes. Given a note path, extract its claims about backend behavior, read the referenced .py file(s), and produce a line-by-line diff of what the note says vs what the code does. More precise than Find-Contradictions — use this when you already know which note to check.
when_to_use: when you want to check a SPECIFIC note against the code it references, after editing a note that describes backend behavior, or to verify a single note before trusting it
falsifiable_if: the diff reports a mismatch that doesn't exist, or misses a real mismatch between the note and code
applies_to:
  - vault-code-sync
  - documentation-accuracy
  - diffing
  - verification
allowed_tools:
  - code_read
  - llm_generate
summary: Note-vs-Code-Diff
tags:
  - procedure
  - procedures
---

# Note-vs-Code-Diff

## When to Run This

Run this when you need to check a **specific** note against the code it
describes. Unlike [[Find-Contradictions]] which scans the whole vault,
this takes one note path and produces a precise claim-by-claim diff.

## Why This Exists

A note describing backend behavior can drift from the code it documents.
This procedure produces a claim-by-claim diff of what the note says vs what
the code does. The tradeoff: it is more precise than Find-Contradictions but
requires you to already know which note to check.

## Steps

### Step 1: Read the note and extract backend-behavior claims

1. ```python
import json

note_path = args.get("note_path", "")
if not note_path:
    result = json.dumps({"error": "note_path argument required"})
else:
    p = Path(vault_path) / note_path
    if not p.exists():
        # Try resolving via vault root
        p = Path(note_path)
    if not p.exists():
        result = json.dumps({"error": f"note not found: {note_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        result = json.dumps({"note_path": str(p), "text": text[:2000]})
```

### Step 2: Small model extracts claims and maps them to code

2. ```python
import json as _json, re

note_data = _json.loads(output)
if "error" in note_data:
    result = output
else:
    text = note_data["text"]
    # Find .py references
    py_files = list(set(re.findall(r'[\w_/]+\.py', text)))
    backend_dir = Path(vault_path) / "vaultbot" / "vaultbot_backend"
    code_chunks = []
    for pyf in py_files[:3]:
        cp = backend_dir / pyf
        if not cp.exists():
            matches = list(backend_dir.rglob(Path(pyf).name))
            cp = matches[0] if matches else None
        if cp and cp.exists():
            code_chunks.append({
                "file": str(cp.relative_to(backend_dir)),
                "content": cp.read_text(encoding="utf-8", errors="replace")[:2000],
            })
    prompt = f"""Extract every claim from this note that describes how the backend works.
For each claim, state what the note says and check it against the code.
Note text:
{text}

Code:
{_json.dumps(code_chunks)}

Return a JSON array of objects:
[{{"claim": "what the note says", "code_file": "file.py", "code_says": "what the code actually does", "verdict": "match|mismatch|unverifiable"}}]
Return ONLY the JSON array."""
    diff = llm_generate(prompt)
    result = diff
```

### Step 3: Parse and format the diff

3. ```python
import json as _json

try:
    start = output.find("[")
    end = output.rfind("]")
    if start != -1 and end > start:
        claims = _json.loads(output[start:end+1])
    else:
        claims = []
except Exception:
    claims = []

mismatches = [c for c in claims if c.get("verdict") == "mismatch"]
matches = [c for c in claims if c.get("verdict") == "match"]
unverifiable = [c for c in claims if c.get("verdict") == "unverifiable"]

result = _json.dumps({
    "total_claims": len(claims),
    "matches": len(matches),
    "mismatches": mismatches,
    "unverifiable": unverifiable,
    "mismatch_count": len(mismatches),
})
```

## Related

- [[Find-Contradictions]] — vault-wide note-vs-code contradiction scan
- [[Note-Accuracy-Check]] — verifies claims against the vault
- [[Code-Diff-Notes]] — diffs notes against code