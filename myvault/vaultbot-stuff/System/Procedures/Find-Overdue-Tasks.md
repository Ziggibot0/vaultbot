---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-01
description: "Find open tasks/checkboxes (- [ ]) scattered across notes, oldest first. Imports the Pattern-Scan engine, filters to notes with todo_count > 0, and surfaces which notes carry the most unfinished tasks. Use when asked what's left to do or what tasks are lingering."
when_to_use: when asked what's left to do, what tasks/todos are outstanding, or to find lingering unfinished work across notes
falsifiable_if: a note reported as having open tasks actually has none, or known checkbox-heavy notes are omitted
applies_to:
  - pattern-recognition
  - tasks
  - vault-maintenance
allowed_tools:
  - run_procedure
  - vault_list
provides:
  - Pattern-Scan
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Find-Overdue-Tasks

## When to Run This

Run to collect every open `- [ ]` checkbox across all notes, surfaced by
the notes carrying the most unfinished tasks. A thin filter over
[[Pattern-Scan]] (`todo_count > 0`), then ranked. Good for "what's still
on my plate?" without the big model hunting through notes.

## Why This Exists

Open tasks are scattered across notes as `- [ ]` checkboxes, and answering "what's left to do" requires collecting them all without the big model hunting through notes. This procedure exists as a thin filter over [[Pattern-Scan]] (`todo_count > 0`), ranked by most open tasks then oldest. The key tradeoff is that it surfaces the actual task text from the worst offenders, so the user sees the real work rather than just counts.

## Steps

### Step 1: Run Pattern-Scan and filter to notes with open tasks

1. ```python
import json

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

with_todos = [
    {"path": r["path"], "dir": r["dir"], "open": r["todo_count"],
     "age_days": r["age_days"]}
    for r in records if r.get("todo_count", 0) > 0
]
# Rank by most open tasks, then oldest
with_todos.sort(key=lambda t: (-t["open"], -t["age_days"]))

total_open = sum(t["open"] for t in with_todos)

# Now extract the actual task lines from the worst offenders for context
top_offenders = []
for t in with_todos[:12]:
    try:
        text = Path(vault_path, t["path"]).read_text(encoding="utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if "- [ ]" in ln]
        top_offenders.append({"path": t["path"], "tasks": lines[:10]})
    except Exception:
        continue

result = json.dumps({
    "notes_with_open_tasks": len(with_todos),
    "total_open_tasks": total_open,
    "by_note": with_todos[:30],
    "top_offender_tasks": top_offenders,
})
```

### Step 2: Report outstanding tasks, oldest and heaviest first

2. [llm: Report the outstanding tasks from the prior step output. Lead with total open task count and how many notes carry them. Then list the heaviest notes (most open tasks) with their actual task text so the user sees the real work. Group by folder if it helps. End by suggesting which 1-3 tasks look oldest/most neglected and worth doing first.]

### Step 3: Validate

3. [validate: contains "task"]

## Related

- [[Pattern-Scan]] — the engine this filters
- [[Dream-TODO-Track]] — sibling task-tracking via TODO markers
- [[Count-Todos]] — counts TODO markers across the vault
