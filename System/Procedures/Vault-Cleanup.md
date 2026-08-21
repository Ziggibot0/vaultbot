---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-01
description: "Meta cleanup audit. Runs the Pattern-Scan engine ONCE, then reads its table to produce a single prioritized cleanup queue: orphans to link, broken links to fix, duplicates to merge, stubs to expand, load-bearing stale notes to refresh. One report, zero big-model reasoning over raw notes — all filtering is deterministic."
when_to_use: when asked to clean up the vault, do a vault hygiene/health sweep, or produce a single prioritized to-do list of maintenance work
falsifiable_if: the cleanup queue recommends an action contradicted by the note's actual state (e.g. merge a non-duplicate)
applies_to:
  - pattern-recognition
  - vault-maintenance
  - meta-procedure
  - cleanup
allowed_tools:
  - run_procedure
  - vault_list
summary: Vault-Cleanup
tags:
  - procedure
  - procedures
---

# Vault-Cleanup

## When to Run This

Run when the operator asks to "clean up the vault" or for a single
prioritized maintenance to-do list. This is a **meta-procedure**: it runs
[[Pattern-Scan]] once and derives every cleanup category from that one
table — orphans, broken links, duplicates, stubs, stale load-bearing
notes. It does NOT re-run each Find-* scanner separately (that would
rescan the vault 5×); it reads the shared JSON.

## Why This Exists

Cleanup work was scattered across separate Find-* scanners, each rescanning the vault and producing its own report. This procedure exists to run Pattern-Scan once and derive every cleanup category from that single table. The key tradeoff: all filtering is deterministic — zero big-model reasoning over raw notes — so the report is cheap and consistent.

## Steps

### Step 1: Run Pattern-Scan and derive every cleanup category from one table

1. ```python
import json

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])
counts = data.get("counts", {})
dupes = data.get("duplicates", {})

orphans = [r["path"] for r in records if r.get("is_orphan") and not r.get("is_daily")]
broken_notes = sorted(
    ({"path": r["path"], "broken": r["broken_links"]} for r in records if r.get("unresolved_out", 0) > 0),
    key=lambda b: -len(b["broken"]))
dup_groups = [
    {"title": s, "paths": ps} for s, ps in dupes.items()
]
stubs = [r["path"] for r in records
         if (r.get("is_thin") or r.get("is_stub")) and not r.get("is_daily") and not r.get("is_procedure")]
stale_loadbearing = [
    {"path": r["path"], "age_days": r["age_days"], "links_in": r["links_in_count"]}
    for r in records
    if r.get("is_stale") and not r.get("is_daily") and r.get("links_in_count", 0) >= 5
]
stale_loadbearing.sort(key=lambda s: -s["links_in"])

queue = {
    "health": counts,
    "link_orphans": {"count": len(orphans), "notes": orphans[:20],
                     "action": "run Find-Orphans then Note-Linker / Dream-Pass"},
    "fix_broken_links": {"count": counts.get("with_broken_links", 0),
                         "notes": broken_notes[:15],
                         "action": "run Find-Broken-Links; create most-wanted missing notes or fix typos"},
    "merge_duplicates": {"count": len(dup_groups), "groups": dup_groups[:15],
                          "action": "run Find-Duplicates; merge into canonical keeper"},
    "expand_stubs": {"count": len(stubs), "notes": stubs[:20],
                     "action": "run Find-Stubs; expand load-bearing ones first"},
    "refresh_stale": {"count": len(stale_loadbearing), "notes": stale_loadbearing[:15],
                       "action": "run Find-Stale-Notes; re-verify facts via Check-Entailment"},
}

# Rough priority score so the LLM reports an ordered list, not a wall
priority = []
if broken_notes: priority.append(("fix_broken_links", counts.get("total_broken_links", 0)))
if stale_loadbearing: priority.append(("refresh_stale", len(stale_loadbearing)))
if dup_groups: priority.append(("merge_duplicates", len(dup_groups)))
if orphans: priority.append(("link_orphans", len(orphans)))
if stubs: priority.append(("expand_stubs", len(stubs)))
queue["priority_order"] = [p[0] for p in sorted(priority, key=lambda x: -x[1])]

result = json.dumps(queue)
```

### Step 2: Present a single prioritized cleanup queue

2. [llm: Present the cleanup queue from the prior step output as ONE ordered, prioritized to-do list for the operator. Lead with overall vault health counts (total notes, orphans, broken links, duplicates, stubs, open tasks). Then walk the priority_order, giving each category a one-line summary, its count, 2-3 example notes, and the recommended next procedure to run. End with a concrete "do this first" recommendation. Do NOT do the work — this is the audit/triage report.]

### Step 3: Validate

3. [validate: contains "cleanup" or contains "priority"]

## Related

- [[Pattern-Scan]] — the engine this meta-procedure reads from
- [[Vault-Health-Check]] — the fast health snapshot that points here
- [[Vault-Gaps]] — derives gaps from the same Pattern-Scan table
