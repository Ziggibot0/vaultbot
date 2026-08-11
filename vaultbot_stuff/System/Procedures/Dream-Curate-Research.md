---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-09
description: "Curate raw research notes: evaluate quality, upgrade status, add missing wikilinks, flag thin/duplicate notes for merge or deletion. Called by Dream-Pass to convert the backlog of raw research notes into connected knowledge."
when_to_use: as part of a Dream-Pass cycle, or standalone when the research note backlog needs curation
applies_to:
  - vault
  - research
  - curation
  - dream-pass
allowed_tools:
  - vault_list
  - vault_read_note
  - vault_lint
  - vault_search
  - md_safe_replace
  - vault_safe_write
falsifiable_if: it upgrades a note that is actually low-quality, misses notes that need curation, or breaks wikilinks during repair
success_count: 0
failure_count: 0
success_rate: 0.0
summary: Dream-Curate-Research
tags:
  - procedure
  - procedures
---

# Dream-Curate-Research

Curates the backlog of raw research notes (type: research, status: raw) that accumulate from the autonomous researcher. Evaluates each note on quality signals, upgrades status where warranted, adds missing wikilinks, and flags thin/duplicate notes for merge or deletion.

## Quality Signals

A research note is "curatable" (ready to upgrade from `raw` to `active`) when it has:
- **At least 3 wikilinks** to other vault notes (connected to the graph)
- **At least 500 chars** of body content (not just a summary line)
- **At least 3 sources** cited (grounded in external evidence)
- **No broken wikilinks** (all links resolve)

## Step 1: Find all raw research notes

1. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

raw_notes = []
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
        
        if not content.startswith("---"):
            continue
        
        # Parse frontmatter
        fm_end = content.find("---", 3)
        if fm_end == -1:
            continue
        fm = content[3:fm_end]
        
        if "type: research" not in fm:
            continue
        if "status: raw" not in fm:
            continue
        
        body = content[fm_end+3:].strip()
        # Count wikilinks in body
        wikilinks = re.findall(r'\[\[([^\]]+)\]\]', body)
        # Count sources (lines with http or archived)
        sources = re.findall(r'https?://', body)
        
        raw_notes.append({
            "path": os.path.relpath(fpath, vault_path),
            "title": os.path.splitext(f)[0],
            "chars": len(body),
            "wikilinks": len(wikilinks),
            "sources": len(sources),
            "has_summary": "summary" in fm,
            "has_tags": "tags:" in fm,
        })

print(f"Found {len(raw_notes)} raw research notes")
```

## Step 2: Score and classify each raw note

2. ```python
# --- Score each note on curatability ---
curatable = []
needs_work = []
thin_or_junk = []

for note in raw_notes:
    score = 0
    if note["wikilinks"] >= 3:
        score += 1
    if note["chars"] >= 500:
        score += 1
    if note["sources"] >= 3:
        score += 1
    
    note["curation_score"] = score
    
    if score >= 3:
        curatable.append(note)
    elif score >= 1:
        needs_work.append(note)
    else:
        thin_or_junk.append(note)

# Sort curatable by chars (largest first — most content to work with)
curatable.sort(key=lambda n: -n["chars"])
needs_work.sort(key=lambda n: -n["chars"])
thin_or_junk.sort(key=lambda n: n["chars"])

print(f"Curatable (score >= 3): {len(curatable)}")
print(f"Needs work (score 1-2): {len(needs_work)}")
print(f"Thin/junk (score 0): {len(thin_or_junk)}")
```

## Step 3: Upgrade curatable notes from raw to active

3. ```python
# --- For each curatable note, upgrade status: raw -> active ---
upgraded = []
for note in curatable[:20]:  # Cap at 20 per pass to avoid overwhelming changes
    full_path = os.path.join(vault_path, note["path"])
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace status: raw with status: active
        new_content = content.replace("status: raw", "status: active", 1)
        
        if new_content != content:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            upgraded.append(note["title"])
            print(f"  Upgraded: {note['title']} (score={note['curation_score']}, chars={note['chars']}, links={note['wikilinks']})")
    except Exception as e:
        print(f"  Failed: {note['title']} — {e}")

# --- For needs_work notes, add a curation hint ---
hints = []
for note in needs_work[:10]:
    missing = []
    if note["wikilinks"] < 3:
        missing.append(f"needs {3 - note['wikilinks']} more wikilinks")
    if note["chars"] < 500:
        missing.append(f"needs {500 - note['chars']} more chars")
    if note["sources"] < 3:
        missing.append(f"needs {3 - note['sources']} more sources")
    hints.append({
        "title": note["title"],
        "path": note["path"],
        "score": note["curation_score"],
        "missing": missing
    })

result = json.dumps({
    "status": "completed",
    "total_raw": len(raw_notes),
    "curatable": len(curatable),
    "upgraded": len(upgraded),
    "upgraded_notes": upgraded,
    "needs_work": len(needs_work),
    "needs_work_hints": hints,
    "thin_or_junk": len(thin_or_junk),
    "thin_examples": [n["title"] for n in thin_or_junk[:10]],
}, indent=2)
```

## Step 4: Validate

4. [validate: contains "upgraded"]
