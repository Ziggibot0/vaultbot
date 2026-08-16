---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-01
description: Find stale notes — notes not modified in over 30 days that may be outdated. Imports the Pattern-Scan engine and filters to is_stale=true, excluding daily notes. Use to find knowledge that needs refreshing or re-verification.
when_to_use: when asked which notes are old/stale/outdated/need refreshing, or during periodic re-verification passes
falsifiable_if: a reported stale note was recently edited, or known old notes are omitted
applies_to:
  - pattern-recognition
  - vault-maintenance
  - freshness
allowed_tools:
  - run_procedure
  - vault_list
provides:
  - Pattern-Scan
summary: Find-Stale-Notes
tags:
  - procedure
  - procedures
---

# Find-Stale-Notes

## When to Run This

Run to find notes that haven't been touched in over 30 days. Stale
factual notes may be outdated; stale plans may be abandoned. Thin filter
over [[Pattern-Scan]] (`is_stale == true`), excluding daily journal notes
(which are meant to be immutable snapshots).

## Steps

### Step 1: Run Pattern-Scan and filter to stale notes

1. ```python
import json, os

run_procedure("Pattern-Scan")
out_file = str(Path(os.environ.get("VAULT_PATH", ".")) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

stale = []
for r in records:
    if r.get("is_daily"):
        continue  # journals are snapshots, never "stale"
    if r.get("is_stale"):
        stale.append({
            "path": r["path"], "dir": r["dir"], "age_days": r["age_days"],
            "type": r["type"], "links_in": r["links_in_count"],
            "is_orphan": r["is_orphan"],
        })
# Oldest first; well-linked stale notes matter most (they're load-bearing)
stale.sort(key=lambda s: (-s["links_in"], -s["age_days"]))

load_bearing = [s for s in stale if s["links_in"] >= 5]

result = json.dumps({
    "stale_count": len(stale),
    "load_bearing_stale": len(load_bearing),
    "priority_refresh": load_bearing[:20],
    "all_stale": stale[:40],
    "truncated": len(stale) > 40,
})
```

### Step 2: Report stale notes, prioritizing load-bearing factual notes

2. [llm: Report the stale notes from the prior step output. Prioritize "load-bearing" stale notes (linked from 5+ notes) — outdated facts there propagate widely, so re-verify those first (suggest Check-Entailment / Verify-Claims or a research refresh). Separate factual/research notes (worth refreshing) from plans/logs (staleness is fine). Give an ordered refresh list.]

### Step 3: Validate

3. [validate: contains "stale"]
