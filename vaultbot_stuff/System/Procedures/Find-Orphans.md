---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: Find orphan notes — notes with no incoming AND no outgoing resolved wikilinks (disconnected islands). Imports the Pattern-Scan engine and filters its per-note table to is_orphan=true. Use before linking, organizing, or running a dream pass.
when_to_use: when asked which notes are disconnected/orphaned, before linking orphans into the graph, or during vault organization
falsifiable_if: the returned orphans actually have resolved wikilinks, or known isolated notes are missing
applies_to:
  - pattern-recognition
  - vault-maintenance
  - graph-organization
allowed_tools:
  - run_procedure
  - vault_list
summary: Find-Orphans
tags:
  - procedure
  - procedures
---

# Find-Orphans

## When to Run This

Run when you need the list of disconnected notes — notes nothing links to
and that link to nothing resolvable. This is a **thin filter** over the
[[Pattern-Scan]] engine: it imports the engine, reads the per-note table,
and keeps only `is_orphan == true`. Do NOT walk the vault inline.

## Steps

### Step 1: Run the Pattern-Scan engine and load its full table

1. ```python
import json, os

scan = run_procedure("Pattern-Scan")  # returns dict; full data is on disk
out_file = None
try:
    summary = json.loads(scan.get("final_output", "{}"))
    out_file = summary.get("out_file")
except Exception:
    out_file = None
if not out_file or not Path(out_file).exists():
    out_file = str(Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern-scan-latest.json")

data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

orphans = [
    {"path": r["path"], "dir": r["dir"], "chars": r["chars"],
     "is_thin": r["is_thin"], "age_days": r["age_days"]}
    for r in records if r.get("is_orphan")
]
# Sort: procedures/system notes last, big useful notes first
orphans.sort(key=lambda o: (o["dir"] in ("System", "vaultbot_stuff"), -o["chars"]))

result = json.dumps({
    "orphan_count": len(orphans),
    "total_notes": data.get("counts", {}).get("total_notes"),
    "orphans": orphans[:60],
    "truncated": len(orphans) > 60,
})
```

### Step 2: Report the orphans, grouped by folder, with a suggested hub for each

2. [llm: Report the orphan notes from the prior step output. Group them by folder (dir). For each orphan, give the note name and, based on its folder and any obvious topic, suggest ONE existing hub note it could be linked from (e.g. Research notes → a Research index, Chat notes → a chat log index). Keep it scannable. If there are no orphans, say the graph is fully connected.]

### Step 3: Validate

3. [validate: contains "orphan"]
