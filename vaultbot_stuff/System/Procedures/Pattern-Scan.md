---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: Importable pattern-recognition engine. Walks EVERY .md note in the vault and computes ~15 deterministic per-note signals (length, links in/out, orphan/thin/hub status, frontmatter presence, todo/stub markers, duplicate-title groups, daily-note files, staleness) plus vault-wide aggregates. Writes the full per-note table to vaultbot_stuff/Memory/Build-Log/pattern-scan-latest.json and returns a compact summary. Pure code, zero LLM cost — the big model never reasons about raw notes, it reads the filtered output of a domain scanner that calls this.
when_to_use: when any procedure needs to recognize patterns across LOTS of notes at once — do NOT scan the vault inline; call run_procedure(\"Pattern-Scan\") then read pattern-scan-latest.json and filter for your domain. Domain scanners (Find-Orphans, Find-Thin-Notes, Find-Stubs, Find-Duplicates, Find-Broken-Links, Find-Unlinked-Mentions, Find-Overdue-Tasks, Find-Stale-Notes) already do this for you.
falsifiable_if: pattern-scan-latest.json is missing, empty, or its per-note signals contradict what vault_lint / vault_graph_analyzer report for the same notes
applies_to:
  - pattern-recognition
  - vault-maintenance
  - meta-procedure
  - compaction
allowed_tools:
  - vault_list
  - vault_graph_analyzer
summary: Pattern-Scan
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

## Output contract

- **File written:** `vaultbot_stuff/Memory/Build-Log/pattern-scan-latest.json`
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

### Step 1: Walk every note and compute per-note + vault-wide signals

1. ```python
import os, re, json, datetime

vault = str(Path(vault_path).resolve())
out_dir = Path(vault) / "vaultbot_stuff" / "Memory" / "Build-Log"
out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "pattern-scan-latest.json"

WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]')
DAILY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
FM_TYPE_RE = re.compile(r'^type:\s*(.+?)\s*$', re.MULTILINE)
STUB_MARKERS = ("todo", "stub", "placeholder", "expand me", "tbd", "wip")
THIN_THRESHOLD = 500
HUB_THRESHOLD = 8
STALE_DAYS = 30

now = datetime.datetime.now().timestamp()
paths = vault_list()  # absolute .md paths, ignored dirs already excluded

records = []
stem_to_paths = {}
for ap in paths:
    try:
        rel = os.path.relpath(ap, vault).replace("\\", "/")
        text = Path(ap).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    stem = rel.split("/")[-1][:-3]
    top_dir = rel.split("/")[0] if "/" in rel else "(root)"
    has_fm = text.startswith("---")
    fm_type = ""
    if has_fm:
        m = FM_TYPE_RE.search(text[:text.find("\n---", 3) if "\n---" in text[3:] else 2000])
        fm_type = (m.group(1).strip() if m else "")
    links_out = [l.strip() for l in WIKILINK_RE.findall(text)]
    try:
        age_days = (now - os.path.getmtime(ap)) / 86400.0
    except Exception:
        age_days = 0.0
    body_lower = text.lower()
    rec = {
        "path": rel,
        "stem": stem,
        "dir": top_dir,
        "chars": len(text),
        "has_frontmatter": has_fm,
        "type": fm_type,
        "links_out": links_out,
        "links_out_count": len(links_out),
        "links_in_count": 0,
        "is_orphan": False,
        "is_thin": len(text) < THIN_THRESHOLD,
        "is_stub": any(sm in body_lower for sm in STUB_MARKERS),
        "is_hub": False,
        "has_todo": "- [ ]" in text,
        "todo_count": text.count("- [ ]"),
        "is_daily": bool(DAILY_RE.match(stem)),
        "is_procedure": fm_type.lower() == "procedure",
        "broken_links": [],
        "unresolved_out": 0,
        "age_days": round(age_days, 1),
        "is_stale": age_days > STALE_DAYS,
    }
    records.append(rec)
    stem_to_paths.setdefault(stem.lower(), []).append(rel)

# Second pass: backlinks + broken links + orphans + duplicates
by_stem = {r["stem"].lower(): r["path"] for r in records}
all_stems = set(by_stem.keys())
for r in records:
    resolved = 0
    for target in r["links_out"]:
        t = target.split("#")[0].strip().lower()
        if t in all_stems:
            resolved += 1
            by_stem_rec = by_stem[t]
            # increment that record's backlinks
            for rr in records:
                if rr["path"] == by_stem_rec:
                    rr["links_in_count"] += 1
                    break
        elif t:
            r["broken_links"].append(target)
    r["unresolved_out"] = len(r["broken_links"])
    r["is_orphan"] = (r["links_in_count"] == 0 and resolved == 0)
    r["is_hub"] = r["links_in_count"] >= HUB_THRESHOLD

# Duplicate titles (same stem, >1 file)
duplicates = {s: ps for s, ps in stem_to_paths.items() if len(ps) > 1}

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

### Step 2: Confirm the scan file exists and report the headline counts

2. [llm: Report the Pattern-Scan headline counts from the prior step output in one short paragraph: total notes, and the counts for orphans, thin, stubs, notes with open todos, notes with broken links, and stale notes. Do NOT list individual notes — this is the engine summary only; domain scanners report specifics. Always mention the out_file path where the full per-note table was written, and note that domain scanners (Find-Orphans, Find-Stubs, Find-Broken-Links, Find-Overdue-Tasks, etc.) read that file to answer specific questions.]

### Step 3: Validate output

3. [validate: contains "notes"]
