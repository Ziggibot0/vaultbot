---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-09
description: "Scan the vault for TODO markers, group them by topic/note, and produce a prioritized action report. Called by Dream-Pass to surface unfinished work that's scattered across the vault."
when_to_use: as part of a Dream-Pass cycle, or standalone when you need to know what unfinished work exists
applies_to:
  - vault
  - maintenance
  - todos
  - dream-pass
allowed_tools:
  - vault_list
  - code_read
falsifiable_if: it misses TODOs that exist in notes, or reports false positives from code blocks
success_count: 0
failure_count: 0
success_rate: 0.0
summary: Dream-TODO-Track
tags:
  - procedure
  - procedures
---

# Dream-TODO-Track

Scans all vault notes for TODO markers (TODO, FIXME, HACK, XXX, NOTE), groups them by the note they appear in, and produces a prioritized report. Excludes TODOs inside code blocks (``` fences) to avoid false positives from example code.

## Step 1: Scan all notes for TODO markers

1. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

TODO_PATTERN = re.compile(r'(?i)\b(TODO|FIXME|HACK|XXX|NOTE)[\s:]+(.+?)(?=\n|$)', re.MULTILINE)

todos = []
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if not f.endswith(".md"):
            continue
        fpath = os.path.join(root, f)
        try:
            with open(fpath, 'r', encoding='utf-8') as fh:
                content = fh.read()
        except:
            continue
        
        # Strip code blocks to avoid false positives
        clean = re.sub(r'```.*?```', '', content, flags=re.DOTALL)
        
        for match in TODO_PATTERN.finditer(clean):
            marker = match.group(1).upper()
            text = match.group(2).strip()
            # Get line context
            line_start = max(0, match.start() - 50)
            line_end = min(len(clean), match.end() + 50)
            context = clean[line_start:line_end].replace('\n', ' ').strip()
            
            todos.append({
                "note": os.path.relpath(fpath, vault_path),
                "marker": marker,
                "text": text[:120],
                "context": context[:200],
            })

# Group by note
by_note = {}
for t in todos:
    note = t["note"]
    if note not in by_note:
        by_note[note] = []
    by_note[note].append(t)

# Sort notes by TODO count (most TODOs first)
sorted_notes = sorted(by_note.items(), key=lambda x: -len(x[1]))

print(f"Found {len(todos)} TODOs across {len(by_note)} notes")
for note, items in sorted_notes[:15]:
    print(f"  {note}: {len(items)} TODOs")
```

## Step 2: Categorize and prioritize

2. ```python
# --- Categorize TODOs by marker type ---
categories = {"TODO": [], "FIXME": [], "HACK": [], "XXX": [], "NOTE": []}
for t in todos:
    cat = t["marker"]
    if cat in categories:
        categories[cat].append(t)

# --- Prioritize: FIXME > TODO > HACK > XXX > NOTE ---
priority_order = ["FIXME", "TODO", "HACK", "XXX", "NOTE"]

# --- Build a scannable report ---
report_lines = []
report_lines.append(f"# TODO Report — {len(todos)} items across {len(by_note)} notes\n")

for marker in priority_order:
    items = categories.get(marker, [])
    if not items:
        continue
    report_lines.append(f"## {marker} ({len(items)} items)\n")
    for item in items[:20]:  # Cap per category
        report_lines.append(f"- **{item['note']}**: {item['text']}")
    report_lines.append("")

# --- Top 10 notes with most TODOs ---
report_lines.append("## Top Notes by TODO Count\n")
for note, items in sorted_notes[:10]:
    report_lines.append(f"- **{note}** — {len(items)} TODOs: {', '.join(i['text'][:60] for i in items[:3])}")

report = "\n".join(report_lines)

result = json.dumps({
    "status": "completed",
    "total_todos": len(todos),
    "notes_with_todos": len(by_note),
    "by_category": {k: len(v) for k, v in categories.items()},
    "top_notes": [{"note": n, "count": len(items)} for n, items in sorted_notes[:10]],
    "report": report,
}, indent=2)
```

## Step 3: Validate

3. [validate: contains "total_todos"]
