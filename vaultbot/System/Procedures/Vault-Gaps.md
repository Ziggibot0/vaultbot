---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-07-31
description: "Detect the vault's knowledge gaps: dangling wikilinks (concepts linked but no note exists) and thin notes (exist but too short). Use when the user asks what's missing or to decide what to research."
when_to_use: when the user asks about gaps, what's missing, or what to research next
applies_to:
  - vault-maintenance
  - research
  - curriculum
allowed_tools:
  - run_procedure
  - vault_list
summary: Vault-Gaps
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Vault-Gaps

## When to Run This

Run this when the user asks what the vault is missing, what gaps exist, or what should be researched next. Also useful before starting a research session to see where the vault is thin.

Gaps are derived deterministically from the [[Pattern-Scan]] engine table — no live service needed. Two kinds: (a) dangling wikilinks — concepts linked but no note exists (the "most wanted missing" targets, ranked by how many notes reference them), and (b) thin/stub notes that exist but carry little content.

## Steps

### Step 1: Detect gaps from the Pattern-Scan table

1. ```python
import json
from collections import Counter

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

# (a) Dangling-link gaps: missing targets ranked by reference count
target_counts = Counter()
for r in records:
    for t in r.get("broken_links", []):
        target_counts[t.split("#")[0].strip()] += 1
dangling = [{"missing": t, "referenced_by": c} for t, c in target_counts.most_common(25)]

# (b) Thin/stub gaps: notes that exist but are nearly empty
thin = [{"path": r["path"], "chars": r["chars"], "links_in": r["links_in_count"]}
        for r in records
        if (r.get("is_thin") or r.get("is_stub")) and not r.get("is_daily") and not r.get("is_procedure")]
thin.sort(key=lambda n: -n["links_in"])

result = json.dumps({
    "dangling_count": len(target_counts),
    "dangling_gaps": dangling,
    "thin_count": len(thin),
    "thin_gaps": thin[:20],
})
```

2. [llm: Report the gaps to the user. Lead with the DANGLING gaps — the missing note titles referenced by the MOST other notes, since filling those closes the most gaps at once. Then the THIN/STUB gaps — existing notes worth expanding, prioritized by how many notes link to them. For each, say what it is and whether it's worth researching. Group by type.]

### Step 2: Validate

2. [validate: contains "gap"]