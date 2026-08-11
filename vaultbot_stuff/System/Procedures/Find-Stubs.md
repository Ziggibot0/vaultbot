---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: Find thin and stub notes — notes that exist but are nearly empty (< 500 chars) or contain stub markers (TODO/stub/placeholder/expand). Imports the Pattern-Scan engine and filters to is_thin or is_stub. Use to find notes worth expanding or merging.
when_to_use: when asked which notes are too short/thin/stubs/need expanding, or during vault cleanup and quality passes
falsifiable_if: a reported thin note is actually substantial, or known stub notes are omitted
applies_to:
  - pattern-recognition
  - vault-maintenance
  - quality
allowed_tools:
  - run_procedure
  - vault_list
provides:
  - Pattern-Scan
summary: Find-Stubs
tags:
  - procedure
  - procedures
---

# Find-Stubs

## When to Run This

Run to find notes that exist but carry little content — thin notes
(< 500 chars) and stub notes (contain "TODO"/"stub"/"placeholder"/etc).
These are candidates to expand, merge, or delete. Thin filter over
[[Pattern-Scan]] (`is_thin OR is_stub`), excluding daily notes and
procedures (which are legitimately structured differently).

## Steps

### Step 1: Run Pattern-Scan and filter to thin/stub notes

1. ```python
import json, os

run_procedure("Pattern-Scan")
out_file = str(Path(os.environ.get("VAULT_PATH", ".")) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

stubs = []
for r in records:
    if r.get("is_daily") or r.get("is_procedure"):
        continue  # daily notes and procedures are excluded by design
    if r.get("is_thin") or r.get("is_stub"):
        stubs.append({
            "path": r["path"], "dir": r["dir"], "chars": r["chars"],
            "is_thin": r["is_thin"], "is_stub": r["is_stub"],
            "links_in": r["links_in_count"], "is_orphan": r["is_orphan"],
        })
stubs.sort(key=lambda s: (not s["is_orphan"], s["chars"]))

result = json.dumps({
    "stub_count": len(stubs),
    "stubs": stubs[:60],
    "truncated": len(stubs) > 60,
})
```

### Step 2: Report stubs with a recommended action for each

2. [llm: Report the thin/stub notes from the prior step output. For each, recommend one action: EXPAND (has incoming links — others rely on it, worth fleshing out), MERGE (orphaned thin note that duplicates an obvious topic), or REVIEW (unclear). Prioritize stubs that are linked from many notes — those are load-bearing and hurt most. Keep it a scannable list.]

### Step 3: Validate

3. [validate: contains "stub" or contains "thin"]
