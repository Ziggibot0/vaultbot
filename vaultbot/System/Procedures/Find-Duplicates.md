---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-01
description: Find duplicate notes — same title in multiple files, or near-identical stems. Imports the Pattern-Scan engine and reads its duplicates map plus per-note table. Use before merging or when asked if the vault has redundant copies.
when_to_use: when asked to find duplicate/redundant/duplicate-title notes, before merging notes, or during cleanup
falsifiable_if: reported duplicates do not actually share a title, or known duplicated notes are omitted
applies_to:
  - pattern-recognition
  - vault-maintenance
  - quality
allowed_tools:
  - run_procedure
  - vault_list
provides:
  - Pattern-Scan
summary: Find-Duplicates
tags:
  - procedure
  - procedures
---

# Find-Duplicates

## When to Run This

Run to find notes that exist more than once under the same title (or a
near-identical title). Duplicates split backlinks and confuse retrieval.
Thin filter over [[Pattern-Scan]] (its `duplicates` map + the per-note
table), enriched with size/link info so you can pick a canonical survivor.

## Why This Exists

Duplicate notes split backlinks and confuse retrieval, but finding them requires comparing every title against every other. This procedure exists as a thin filter over [[Pattern-Scan]]'s duplicates map, enriched with size/link info. The key tradeoff is that it picks a canonical survivor (most linked, then largest) so merging is decisive rather than ambiguous.

## Steps

### Step 1: Run Pattern-Scan and report duplicate-title groups

1. ```python
import json, os

run_procedure("Pattern-Scan")
out_file = str(Path(os.environ.get("VAULT_PATH", ".")) / "vaultbot" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = {r["path"]: r for r in data.get("notes", [])}
dupes = data.get("duplicates", {})  # stem_lower -> [paths]

groups = []
for stem, paths in dupes.items():
    members = []
    for p in paths:
        r = records.get(p, {})
        members.append({
            "path": p,
            "chars": r.get("chars", 0),
            "links_in": r.get("links_in_count", 0),
            "is_orphan": r.get("is_orphan", False),
        })
    # canonical = most incoming links, then largest
    members.sort(key=lambda m: (-m["links_in"], -m["chars"]))
    groups.append({"title": stem, "keep": members[0]["path"],
                   "merge_or_delete": [m["path"] for m in members[1:]],
                   "members": members})
groups.sort(key=lambda g: -len(g["members"]))

result = json.dumps({
    "duplicate_groups": len(groups),
    "groups": groups[:40],
    "truncated": len(groups) > 40,
})
```

### Step 2: Report duplicates with a recommended canonical note per group

2. [llm: Report the duplicate-title groups from the prior step output. For each group, name the recommended KEEPER (the one already chosen as 'keep' — most linked/largest) and which copies should be merged into it then deleted. Note if any group looks like genuinely different topics that happen to share a title (rename one) rather than true duplicates. Be decisive and actionable.]

### Step 3: Validate

3. [validate: contains "duplicate"]

## Related

- [[Pattern-Scan]] — the engine this filters
- [[Find-Orphans]] — sibling filter over the same engine
- [[Note-Merge-Candidates]] — merges the duplicates this finds
