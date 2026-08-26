---
type: procedure
status: active
baseline: true
created: 2026-08-01
updated: 2026-08-04
description: "THIN ORCHESTRATOR: runs Vault-Walk → computes shared maps (backlinks, broken links, orphans, hubs, duplicates) → applies all signal logic → writes pattern-scan-latest.json. Pure code, zero LLM cost. This is the refactored version — the monolith is decomposed into Vault-Walk (independent per-note signals) + granular signal probes + this thin orchestrator."
when_to_use: when any procedure needs to recognize patterns across LOTS of notes at once — do NOT scan the vault inline; call run_procedure("Pattern-Scan") then read pattern-scan-latest.json and filter for your domain. Domain scanners (Find-Orphans, Find-Thin-Notes, Find-Stubs, Find-Duplicates, Find-Broken-Links, Find-Unlinked-Mentions, Find-Overdue-Tasks, Find-Stale-Notes) already do this for you.
falsifiable_if: pattern-scan-latest.json is missing, empty, or its per-note signals contradict what vault_lint / vault_graph_analyzer report for the same notes
applies_to:
  - pattern-recognition
  - vault-maintenance
  - meta-procedure
  - compaction
allowed_tools:
  - vault_list
  - vault_graph_analyzer
  - run_procedure
summary: Pattern-Scan — thin orchestrator that runs Vault-Walk, computes vault-wide signals, and writes the full per-note table to pattern-scan-latest.json.
tags:
  - procedure
  - procedures
---

# Pattern-Scan

## When to Run This

This is the **vault-wide pattern-recognition engine**. It is the single
importable procedure that recognizes different kinds of patterns across
LOTS of notes in one deterministic pass. Simple checking procedures do
NOT re-walk the vault — they call `run_procedure("Pattern-Scan")`, read
the JSON it writes, and filter for their specific concern.

Run Pattern-Scan directly only when you want the raw per-note table. For
a specific question ("which notes are orphans?", "which todos are
overdue?"), call the matching domain scanner instead — it imports this.

## Why This Exists (compounding design)

The 30B big model should never burn context reasoning over hundreds of
raw notes. This procedure does the walking deterministically and emits a
structured table. Every domain checker is then a ~10-line filter over
that table — so each new "find X pattern" capability costs almost no
tokens to create and none to run. The procedure system compounds: one
scan, many cheap checks.

## Architecture (refactored)

This is now a **thin orchestrator** — the 213-line monolith is decomposed into:

| Layer | What it does | LLM cost |
|---|---|---|
| **Vault-Walk** (called as sub-procedure) | Reads every .md, returns raw per-note data (path, stem, text, chars, links_out, frontmatter, age_days) | Zero |
| **This orchestrator** | Runs Vault-Walk → computes shared maps (backlinks, broken links, orphans, hubs, duplicates) → applies all signal logic → writes JSON | Zero |
| **Signal probes** (standalone, reusable) | Each is a ~10-line filter over Vault-Walk output: Check-Thin, Check-Stub, Count-Todos, Check-Daily, Check-Procedure-Type, Check-Staleness | Zero |

The signal probes exist as standalone procedures so domain scanners can call them independently without re-walking the vault. This orchestrator applies the same logic inline for efficiency.

## Output contract

- **File written:** `vaultbot-stuff/Memory/Build-Log/pattern-scan-latest.json`
  — the FULL per-note table + aggregates. Parents read this, NOT
  `final_output` (the CLI truncates `final_output` to 4000 chars).
- **Return value (final step `result`):** a compact summary JSON:
  `{counts, out_file, generated_at}`.

Each note record has these signal fields:

| field | meaning |
|---|---|
| `path` | vault-relative path (forward slashes) |
| `stem` | note title without `.md` |
| `dir` | top-level folder |
| `chars` | file size in characters |
| `has_frontmatter` | bool |
| `type` | frontmatter `type:` value ("" if none) |
| `links_out` | list of wikilink targets (stems) |
| `links_out_count` | int |
| `links_in_count` | int (backlinks, computed vault-wide) |
| `is_orphan` | no incoming AND no outgoing resolved links |
| `is_thin` | `chars` < 500 |
| `is_stub` | body has stub markers ("TODO", "stub", "placeholder", "expand") |
| `is_hub` | `links_in_count` >= 8 |
| `has_todo` | contains `- [ ]` open checkboxes |
| `todo_count` | number of open checkboxes |
| `is_daily` | filename matches `YYYY-MM-DD.md` |
| `is_procedure` | frontmatter `type: procedure` |
| `broken_links` | wikilinks with no matching note in the vault |
| `unresolved_out` | count of broken_links |
| `age_days` | days since file mtime |
| `is_stale` | `age_days` > 30 |

## Steps

### Step 1: Run Vault-Walk to get raw per-note data

1. ```python
import json
from pathlib import Path

# Call Vault-Walk as a sub-procedure — this is the foundation layer
# that reads every .md and returns raw per-note data
walk_result = run_procedure("Vault-Walk")
records = json.loads(walk_result)
print(f"Vault-Walk returned {len(records)} notes")
```

### Step 2: Compute vault-wide signals (backlinks, broken links, orphans, hubs, duplicates)

2. ```python
import json, datetime, re
from pathlib import Path

# records is from Step 1
vault = str(Path(vault_path).resolve())
out_dir = Path(vault) / "vaultbot-stuff" / "Memory" / "Build-Log"
out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "pattern-scan-latest.json"

# --- Constants (same as original) ---
THIN_THRESHOLD = 500
HUB_THRESHOLD = 8
STALE_DAYS = 30
STUB_MARKERS = ("todo", "stub", "placeholder", "expand me", "tbd", "wip")
DAILY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# --- Build shared maps ---
# stem → path (for backlink resolution)
by_stem = {}
for r in records:
    by_stem[r["stem"].lower()] = r["path"]

all_stems = set(by_stem.keys())

# stem → list of paths (for duplicate detection)
stem_to_paths = {}
for r in records:
    stem_lower = r["stem"].lower()
    stem_to_paths.setdefault(stem_lower, []).append(r["path"])

# --- Second pass: backlinks + broken links ---
# Initialize vault-wide fields
for r in records:
    r["links_in_count"] = 0
    r["broken_links"] = []
    r["unresolved_out"] = 0
    r["is_orphan"] = False
    r["is_hub"] = False

for r in records:
    resolved = 0
    for target in r["links_out"]:
        t = target.split("#")[0].strip().lower()
        if t in all_stems:
            resolved += 1
            target_path = by_stem[t]
            # increment that record's backlinks
            for rr in records:
                if rr["path"] == target_path:
                    rr["links_in_count"] += 1
                    break
        elif t:
            r["broken_links"].append(target)
    r["unresolved_out"] = len(r["broken_links"])
    r["is_orphan"] = (r["links_in_count"] == 0 and resolved == 0)
    r["is_hub"] = r["links_in_count"] >= HUB_THRESHOLD

# --- Apply signal logic (same as original, now on records from Vault-Walk) ---
# Vault-Walk gives us: path, stem, dir, chars, has_frontmatter, type, links_out, links_out_count, age_days
# We need to add: is_thin, is_stub, has_todo, todo_count, is_daily, is_procedure, is_stale
# Note: Vault-Walk does NOT include body text, so we need to re-read for stub/todo detection.
# This is a tradeoff: we could have Vault-Walk include body_lower, but that bloats its output.
# Instead, we re-read files here for the signals that need body text.

for r in records:
    # is_thin — from chars (already in Vault-Walk output)
    r["is_thin"] = r["chars"] < THIN_THRESHOLD

    # is_daily — from stem (already in Vault-Walk output)
    r["is_daily"] = bool(DAILY_RE.match(r["stem"]))

    # is_procedure — from type (already in Vault-Walk output)
    r["is_procedure"] = r["type"].lower() == "procedure"

    # is_stale — from age_days (already in Vault-Walk output)
    r["is_stale"] = r["age_days"] > STALE_DAYS

    # has_todo, todo_count, is_stub — need body text
    try:
        ap = Path(vault) / r["path"]
        text = ap.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""

    r["has_todo"] = "- [ ]" in text
    r["todo_count"] = text.count("- [ ]")
    body_lower = text.lower()
    r["is_stub"] = any(sm in body_lower for sm in STUB_MARKERS)

# --- Duplicates ---
duplicates = {s: ps for s, ps in stem_to_paths.items() if len(ps) > 1}

# --- Aggregates ---
counts = {
    "total_notes": len(records),
    "orphans": sum(1 for r in records if r["is_orphan"]),
    "thin": sum(1 for r in records if r["is_thin"]),
    "stubs": sum(1 for r in records if r["is_stub"]),
    "hubs": sum(1 for r in records if r["is_hub"]),
    "with_todos": sum(1 for r in records if r["has_todo"]),
    "open_todos": sum(r["todo_count"] for r in records),
    "daily_notes": sum(1 for r in records if r["is_daily"]),
    "procedures": sum(1 for r in records if r["is_procedure"]),
    "no_frontmatter": sum(1 for r in records if not r["has_frontmatter"]),
    "with_broken_links": sum(1 for r in records if r["unresolved_out"] > 0),
    "total_broken_links": sum(r["unresolved_out"] for r in records),
    "duplicate_title_groups": len(duplicates),
    "stale": sum(1 for r in records if r["is_stale"]),
}

# --- Write output ---
payload = {
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "vault": vault,
    "counts": counts,
    "duplicates": duplicates,
    "notes": records,
}
out_file.write_text(json.dumps(payload, indent=1), encoding="utf-8")

result = json.dumps({
    "counts": counts,
    "out_file": str(out_file),
    "generated_at": payload["generated_at"],
})
```

### Step 3: Confirm the scan file exists and report the headline counts

3. [llm: Report the Pattern-Scan headline counts from the prior step output in one short paragraph: total notes, and the counts for orphans, thin, stubs, notes with open todos, notes with broken links, and stale notes. Do NOT list individual notes — this is the engine summary only; domain scanners report specifics. Always mention the out_file path where the full per-note table was written, and note that domain scanners (Find-Orphans, Find-Stubs, Find-Broken-Links, Find-Overdue-Tasks, etc.) read that file to answer specific questions.]

### Step 4: Validate output

4. [validate: contains "notes"]

## Related

- [[Vault-Walk]] — the foundation layer that reads every note
- [[Find-Orphans]] — domain scanner that filters this for orphans
- [[Find-Stale-Notes]] — domain scanner that filters this for stale notes
- [[Find-Stubs]] — domain scanner that filters this for thin/stub notes
