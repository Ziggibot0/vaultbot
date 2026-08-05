---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Diff two files (vault note vs vault note, or code vs code) and summarize what changed. Given two file paths, reads both, computes a line-level diff, and has the small model summarize the meaningful changes (not whitespace). Use when comparing two versions of a note, checking what a procedure changed, or verifying a code edit.
when_to_use: when comparing two versions of a file, when checking what changed between two notes, when verifying a code edit did what it should, or when asked 'what's the difference between X and Y'
falsifiable_if: the diff summary reports changes that aren't there, or misses meaningful changes
applies_to:
  - diffing
  - comparison
  - verification
  - vault-maintenance
allowed_tools:
  - code_read
  - llm_generate
summary: Code-Diff-Notes
tags:
  - procedure
  - procedures
---

# Code-Diff-Notes

## When to Run This

Run this when you need to compare two files and understand what changed.
Works on any text files — vault notes, code, procedures.

## Steps

### Step 1: Read both files and compute a line-level diff

1. ```python
import json, difflib

file_a = args.get("file_a", "")
file_b = args.get("file_b", "")
if not file_a or not file_b:
    result = json.dumps({"error": "file_a and file_b arguments required"})
else:
    pa = Path(file_a)
    pb = Path(file_b)
    # Try resolving relative to vault
    if not pa.exists():
        pa = Path(vault_path) / file_a
    if not pb.exists():
        pb = Path(vault_path) / file_b
    if not pa.exists() or not pb.exists():
        result = json.dumps({"error": f"file not found: {file_a if not pa.exists() else file_b}"})
    else:
        lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines()
        lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines()
        diff = list(difflib.unified_diff(lines_a, lines_b,
                                         fromfile=str(pa.name), tofile=str(pb.name),
                                         lineterm=""))
        added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
        result = json.dumps({
            "file_a": str(pa), "file_b": str(pb),
            "lines_added": len(added), "lines_removed": len(removed),
            "added": added[:30], "removed": removed[:30],
            "diff": "\n".join(diff[:80]),
        })
```

### Step 2: Small model summarizes the meaningful changes

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Summarize the MEANINGFUL changes between these two files.
Ignore whitespace, formatting, and trivial changes. Focus on what
substantively changed.

File A: {data['file_a']}
File B: {data['file_b']}
Lines added: {data['lines_added']}, Lines removed: {data['lines_removed']}

Added lines:
{chr(10).join(data['added'][:20])}

Removed lines:
{chr(10).join(data['removed'][:20])}

Return JSON: {{"summary": "2-3 sentence summary of meaningful changes", "breaking": true/false, "changes": ["list of specific changes"]}}
Return ONLY the JSON."""
    summary = llm_generate(prompt)
    result = summary
```

### Step 3: Return the diff summary

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"summary": "could not parse diff summary"}
result = _json.dumps({"diff_stats": {"added": data.get("lines_added", 0),
                                      "removed": data.get("lines_removed", 0)},
                      "summary": parsed})
```