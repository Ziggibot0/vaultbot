---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Check if the vaultbot's self-model (identity notes) matches what it actually does. Reads the identity/self-model notes and compares their claims about capabilities, behavior, and architecture against the actual code. Returns mismatches where the self-model is wrong about itself. Use when the self-model feels stale or after capability changes."
when_to_use: "when the self-model is stale, after adding or removing capabilities, when identity notes don't match reality, or when asked 'does the vaultbot know what it can do'"
falsifiable_if: "the procedure reports a mismatch that isn't real, or misses a real self-model inaccuracy"
applies_to:
  - self-modification
  - self-model
  - identity
  - vault-code-sync
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
---

# Self-Model-Check

## When to Run This

The vaultbot's self-model (identity notes in System/Identity/) describes
what it thinks it can do. After capability changes, this can go stale.
Run this to check if the self-model matches reality.

## Steps

### Step 1: Read identity/self-model notes

1. ```python
import json

identity_dir = Path(vault_path) / "vaultbot_stuff" / "System" / "Identity"
if not identity_dir.exists():
    # Search for self-model notes
    identity_dir = Path(vault_path) / "vaultbot_stuff" / "System"

notes = []
for p in identity_dir.rglob("*.md"):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    # Look for self-model / identity content
    if any(kw in text.lower() for kw in ["self-model", "identity", "i am", "my capabilities",
                                          "what i can do", "how i work", "architecture"]):
        notes.append({"path": rel, "text": text[:2000]})

# Also check for a self-model note specifically
for fp in vault_list():
    p = Path(fp)
    if "self" in p.stem.lower() or "identity" in p.stem.lower():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            rel = str(p.relative_to(vault_path)).replace("\\", "/")
            if rel not in [n["path"] for n in notes]:
                notes.append({"path": rel, "text": text[:2000]})
        except Exception:
            continue

result = json.dumps({"identity_notes": notes[:5], "total": len(notes)})
```

### Step 2: Check self-model claims against actual code

2. ```python
import json as _json, re

data = _json.loads(output)
notes = data.get("identity_notes", [])
if not notes:
    result = _json.dumps({"mismatches": [], "note": "no identity notes found"})
else:
    # Get actual backend capabilities
    backend_dir = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend"
    actual_tools = []
    for py in backend_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Find tool definitions
        for m in re.finditer(r'"name":\s*"(\w+)"', text):
            actual_tools.append(m.group(1))
    actual_tools = list(set(actual_tools))

    mismatches = []
    for note in notes:
        prompt = f"""Check if this self-model/identity note accurately describes
the vaultbot's ACTUAL capabilities.

Note ({note['path']}):
{note['text']}

Actual registered tools: {actual_tools[:30]}

Return JSON: {{"accurate": true/false, "mismatches": [{{"claim": "what the note says", "reality": "what's actually true", "type": "overstates|understates|wrong"}}]}}
Return ONLY the JSON."""
        check = llm_generate(prompt)
        try:
            start = check.find("{")
            end = check.rfind("}")
            parsed = _json.loads(check[start:end+1])
            if not parsed.get("accurate", True):
                mismatches.append({"note": note["path"],
                                   "mismatches": parsed.get("mismatches", [])})
        except Exception:
            continue
    result = _json.dumps({"mismatches": mismatches, "notes_checked": len(notes)})
```

### Step 3: Return the self-model check

3. ```python
import json as _json

data = _json.loads(output)
mismatches = data.get("mismatches", [])
result = _json.dumps({
    "self_model_accurate": len(mismatches) == 0,
    "mismatches": mismatches,
    "notes_checked": data.get("notes_checked", 0),
})
```