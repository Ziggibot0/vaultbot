---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Find vault notes that reference code files, functions, or variables that no longer exist in the backend. Scans notes for .py file references, function names, and class names, then checks each against the actual code. Returns notes with stale references (file deleted, function renamed, class removed). Distinguishes between historical records and active references.
when_to_use: after a refactor that renamed or deleted files/functions, when notes reference code that might not exist anymore, or when cleaning up stale documentation
falsifiable_if: a flagged stale reference actually exists in the code, or a real stale reference is missed
applies_to:
  - vault-code-sync
  - stale-references
  - documentation-accuracy
  - vault-maintenance
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
summary: "STALE-Code-Reference|refactoring, vault notes analysis, function names identification

```json
"summary": "Identifies and fixes stale Python code references found in refactored project notes by scanni"
tags:
  - procedure
  - procedures
---

# Stale-Code-Reference

## When to Run This

Run after a refactor. Notes that reference `old_function_name` or
`deleted_file.py` become stale. This finds them.

## Why This Exists

After a refactor renames or deletes files and functions, vault notes that reference the old names become stale and misleading. This procedure closes that gap by scanning notes for code references and checking each against the actual code. The tradeoff is that it distinguishes historical records from active references, so changelogs and build logs are not falsely flagged.

## Steps

### Step 1: Collect all code references from vault notes

1. ```python
import re, json

all_files = vault_list()
backend_dir = Path(vault_path) / "vaultbot" / "vaultbot_backend"
# Get all actual .py files and their function/class names
actual_files = set()
actual_symbols = set()
for py in backend_dir.rglob("*.py"):
    actual_files.add(py.name)
    try:
        text = py.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'^\s*(?:def|class)\s+(\w+)', text, re.MULTILINE):
            actual_symbols.add(m.group(1))
    except Exception:
        continue

# Scan vault notes for references
stale = []
for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    # Skip procedures and pure history notes
    if "/Procedures/" in rel:
        continue
    py_refs = set(re.findall(r'([\w_]+\.py)', text))
    func_refs = set(re.findall(r'(?:function|def|method|call)\s+(\w+)\s*\(', text, re.IGNORECASE))
    # Check for stale .py references
    stale_files = py_refs - actual_files
    stale_funcs = func_refs - actual_symbols
    if stale_files or stale_funcs:
        stale.append({"note": rel, "stale_files": list(stale_files)[:5],
                      "stale_symbols": list(stale_funcs)[:5]})

result = json.dumps({"stale_references": stale[:30], "total_actual_files": len(actual_files),
                     "total_actual_symbols": len(actual_symbols)})
```

### Step 2: Small model filters out historical/record notes

2. ```python
import json as _json

data = _json.loads(output)
stale = data.get("stale_references", [])
if not stale:
    result = _json.dumps({"stale": [], "note": "no stale references found"})
else:
    # Check each note: is it a historical record or an active reference?
    active_stale = []
    for s in stale:
        p = Path(vault_path) / s["note"]
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:800]
        except Exception:
            continue
        prompt = f"""Does this note reference code files/functions as CURRENT behavior,
or is it a historical record (changelog, build log, past state)?
Note: {s['note']}
Content: {text}
Stale refs: {s['stale_files'] + s['stale_symbols']}

Return JSON: {{"is_historical": true/false, "reason": "..."}}
Return ONLY the JSON."""
        verdict = llm_generate(prompt)
        try:
            start = verdict.find("{")
            end = verdict.rfind("}")
            parsed = _json.loads(verdict[start:end+1])
            if not parsed.get("is_historical", False):
                active_stale.append(s)
        except Exception:
            active_stale.append(s)  # assume active if can't tell
    result = _json.dumps({"active_stale": active_stale,
                          "historical_skipped": len(stale) - len(active_stale)})
```

### Step 3: Report stale references

3. [llm: Format the active stale references from the prior step as a report. For each, show the note path, the stale file/symbol references, and suggest whether to update the note or delete the stale reference. If none, say all code references are current.]

## Related

- [[Update-Vault-References]] — updates references after a rename
- [[Check-Staleness]] — flags stale notes generally
- [[Find-Stale-Notes]] — finds load-bearing notes older than 30 days