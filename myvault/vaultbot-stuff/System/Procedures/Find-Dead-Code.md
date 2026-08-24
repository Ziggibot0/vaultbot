---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Find dead code in the backend: functions and classes that are defined but never called or imported anywhere. Scans all .py files for def/class definitions, then checks if each is referenced anywhere else. Returns a list of unreferenced symbols with their locations. Use before refactoring or when cleaning up."
when_to_use: when cleaning up the backend, before a refactor, when looking for code to remove, or when asked 'what code is unused'
falsifiable_if: a flagged function is actually called (via dynamic dispatch, string eval, etc.), or a truly dead function is missed
applies_to:
  - self-modification
  - dead-code
  - cleanup
  - code-quality
allowed_tools:
  - code_read
  - llm_generate
summary: Find-Dead-Code
tags:
  - procedure
  - procedures
---

# Find-Dead-Code

## When to Run This

Run this to find functions and classes in the backend that nothing calls.
Useful before a refactor or when cleaning up.

## Why This Exists

Dead functions and classes accumulate in the backend and bloat the codebase, but finding them requires checking every definition against every reference. This procedure exists to scan all `.py` files for unreferenced symbols. The key tradeoff is a small-model second pass that filters false positives from dynamic dispatch (getattr, eval, plugin loading), so only genuinely dead code is reported.

## Steps

### Step 1: Collect all definitions and check for references

1. ```python
import re, json

backend_dir = Path(vault_path) / "vaultbot" / "vaultbot_backend"
# Collect all definitions
defs = []  # {name, file, line, kind}
all_text = {}  # file -> text
for py in backend_dir.rglob("*.py"):
    try:
        text = py.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    rel = str(py.relative_to(backend_dir))
    all_text[rel] = text
    for m in re.finditer(r'^(\s*)((?:async\s+)?def|class)\s+(\w+)', text, re.MULTILINE):
        line_num = text[:m.start()].count('\n') + 1
        defs.append({"name": m.group(3), "file": rel, "line": line_num,
                     "kind": m.group(2).replace("async ", "")})

# Check references: a def is "alive" if its name appears in any OTHER file
# or appears as a call/decorator beyond its own definition
dead = []
for d in defs:
    name = d["name"]
    # Skip __init__, main, dunder methods — they're called implicitly
    if name.startswith("__") or name in ("main", "run", "create_app"):
        continue
    found_elsewhere = False
    for rel, text in all_text.items():
        if rel == d["file"]:
            # In same file: check if referenced beyond the def line
            # Count occurrences of the name — if >1, it's called
            count = len(re.findall(rf'\b{re.escape(name)}\b', text))
            if count > 1:
                found_elsewhere = True
                break
        else:
            if re.search(rf'\b{re.escape(name)}\b', text):
                found_elsewhere = True
                break
    if not found_elsewhere:
        dead.append(d)

result = json.dumps({"dead_code": dead[:30], "total_defs": len(defs),
                     "dead_count": len(dead)})
```

### Step 2: Small model filters false positives (dynamic dispatch)

2. ```python
import json as _json

data = _json.loads(output)
dead = data.get("dead_code", [])
if not dead:
    result = _json.dumps({"dead": [], "note": "no dead code found"})
else:
    # Check if any dead functions might be called dynamically
    confirmed = []
    for d in dead:
        prompt = f"""Could this function be called dynamically (getattr, eval,
string dispatch, plugin loading, etc.)?
Function: {d['name']} in {d['file']} L{d['line']}

Return JSON: {{"likely_dynamic": true/false, "reason": "..."}}
Return ONLY the JSON."""
        verdict = llm_generate(prompt)
        try:
            start = verdict.find("{")
            end = verdict.rfind("}")
            parsed = _json.loads(verdict[start:end+1])
            if not parsed.get("likely_dynamic", False):
                confirmed.append(d)
        except Exception:
            confirmed.append(d)  # assume dead if can't tell
    result = _json.dumps({"confirmed_dead": confirmed,
                          "false_positives": len(dead) - len(confirmed)})
```

### Step 3: Report dead code

3. [llm: Format the confirmed dead code from the prior step as a report. For each, show the function/class name, file, and line number. Suggest whether to remove it or keep it (some may be intentional API surfaces). If none, say the backend has no dead code.]

## Related

- [[Find-Contradictions]] — sibling code-quality probe
- [[Check-Dead-Code]] — sibling dead-code check
- [[Code-Audit-Architecture]] — broader code audit