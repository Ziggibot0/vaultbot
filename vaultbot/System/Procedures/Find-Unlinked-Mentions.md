---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-01
description: "Find unlinked mentions — notes whose title is mentioned in plain text in other notes but NOT wrapped in a [[wikilink]]. Imports the Pattern-Scan engine's per-note table, then text-searches for raw mentions of candidate titles. Use to auto-suggest links that weave the graph tighter without creating new notes."
when_to_use: when asked to find linking opportunities, unlinked mentions, or ways to weave the graph tighter without new notes
falsifiable_if: a suggested mention is already wikilinked, or high-value obvious mentions are missed
applies_to:
  - pattern-recognition
  - graph-organization
  - linking
allowed_tools:
  - run_procedure
  - vault_list
provides:
  - Pattern-Scan
summary: Find-Unlinked-Mentions
tags:
  - procedure
  - procedures
---

# Find-Unlinked-Mentions

## When to Run This

Run to find places where a note's **title appears as plain text** in
other notes but is not wrapped in `[[...]]`. Converting these to
wikilinks is the cheapest way to grow graph connectivity — no new notes
needed. Imports [[Pattern-Scan]] for the note list, then does a targeted
text search (only for titles worth checking — hubs and well-linked
notes), keeping cost bounded.

## Why This Exists

A note's title often appears as plain text in other notes without being
wrapped in a wikilink. Converting these is the cheapest way to grow graph
connectivity without creating new notes. The tradeoff: it only checks titles
worth linking (hubs and well-linked notes) to keep cost bounded, so it may
miss low-value mentions.

## Steps

### Step 1: Run Pattern-Scan, then scan for raw (unbracketed) mentions of note titles

1. ```python
import json, re

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

# Candidate titles: substantive notes (not daily, not too-short title),
# that are worth linking to. Long/multiword titles = fewer false positives.
candidates = {}
for r in records:
    stem = r["stem"]
    if r.get("is_daily"):
        continue
    if len(stem) < 6:
        continue  # short titles cause false-positive matches
    candidates[stem] = r

# Build title -> regex (word-boundary, case-insensitive). Precompile.
title_res = {s: re.compile(r'(?<!\[\[)\b' + re.escape(s) + r'\b(?!\]\])', re.IGNORECASE)
             for s in candidates}

# Cache note texts once
texts = {}
for r in records:
    try:
        texts[r["path"]] = Path(vault_path, r["path"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        texts[r["path"]] = ""

WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]')
suggestions = []
for target_stem, rx in title_res.items():
    target_path = candidates[target_stem]["path"]
    for src_path, src_text in texts.items():
        if src_path == target_path:
            continue
        # does src already link to this target?
        existing = {l.split("#")[0].strip().lower() for l in WIKILINK_RE.findall(src_text)}
        if target_stem.lower() in existing:
            continue  # already linked
        if rx.search(src_text):
            suggestions.append({"mention_in": src_path, "link_to": target_path,
                                "title": target_stem})

# Cap + rank: prefer mentions in well-linked source notes and toward hubs
in_count = {r["path"]: r["links_in_count"] for r in records}
suggestions.sort(key=lambda s: -in_count.get(s["link_to"], 0))

result = json.dumps({
    "unlinked_mentions": len(suggestions),
    "suggestions": suggestions[:50],
    "truncated": len(suggestions) > 50,
})
```

### Step 2: Report the top linking opportunities

2. [llm: Report the unlinked-mention link opportunities from the prior step output. List the best ones as "in note X, the phrase 'Y' should become [[Y]]". Prioritize mentions pointing toward hub notes (they strengthen the graph most). Warn about any matches that look like false positives (common English words, partial overlaps). Suggest this could be followed by Note-Linker to actually apply the best ones.]

### Step 3: Validate

3. [validate: contains "link"]

## Related

- [[Pattern-Scan]] — the engine this procedure filters
- [[Note-Linker]] — applies the best link suggestions this finds
- [[Smart-Suggest-Links]] — semantic link suggestion (LLM-based)
