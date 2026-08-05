---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: Find notes containing broken (dangling) wikilinks — links that point to a note that does not exist. Imports the Pattern-Scan engine and filters to notes where unresolved_out > 0, listing each broken target. Use before creating missing notes or cleaning dead links.
when_to_use: when asked to find broken/dead/dangling links, before gap-filling missing notes, or during vault lint
falsifiable_if: a reported broken link actually resolves to a note, or a note with known dead links is omitted
applies_to:
  - pattern-recognition
  - vault-maintenance
  - quality
allowed_tools:
  - run_procedure
  - vault_list
summary: Find-Broken-Links
tags:
  - procedure
  - procedures
---

# Find-Broken-Links

## When to Run This

Run to find every note that contains a wikilink pointing at a note that
doesn't exist yet. These are the vault's dangling references — each is
either a gap to research/fill or a typo to fix. Thin filter over
[[Pattern-Scan]] (`unresolved_out > 0`).

## Steps

### Step 1: Run Pattern-Scan and filter to notes with broken outgoing links

1. ```python
import json

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

bad = [
    {"path": r["path"], "broken": r["broken_links"], "count": r["unresolved_out"]}
    for r in records if r.get("unresolved_out", 0) > 0
]
bad.sort(key=lambda b: -b["count"])

# Also surface the most-referenced missing targets (what to create first)
from collections import Counter
target_counts = Counter()
for r in records:
    for t in r.get("broken_links", []):
        target_counts[t.split("#")[0].strip()] += 1

result = json.dumps({
    "notes_with_broken": len(bad),
    "total_broken": data.get("counts", {}).get("total_broken_links"),
    "most_wanted_missing": target_counts.most_common(15),
    "by_note": bad[:40],
    "truncated": len(bad) > 40,
})
```

### Step 2: Report broken links and prioritize which missing notes to create

2. [llm: Report the broken-link findings from the prior step output. Lead with the "most wanted missing" list — the missing note titles referenced by the MOST other notes — because creating those fills the most gaps at once. Then list the notes containing broken links, each with its dead targets. Distinguish likely-typos (near-miss of an existing title) from genuine gaps. Be actionable.]

### Step 3: Validate

3. [validate: contains "broken"]
