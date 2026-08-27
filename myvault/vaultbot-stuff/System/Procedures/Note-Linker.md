---
type: procedure
status: active
baseline: true
created: 2026-08-01
description: "Suggest wikilinks for the most recently modified note(s). After writing or editing a note, this finds existing vault notes whose titles relate to the new note's content (and vice versa: notes that mention it but don't link it), so the graph stays woven. Deterministic scan + small-model ranking; the LLM only picks which candidates are genuinely related."
when_to_use: right after writing or editing a note — to suggest wikilinks to add so the note connects to the existing graph instead of becoming an orphan
falsifiable_if: suggested links are already present, or obviously-relevant existing notes are not suggested
applies_to:
  - linking
  - graph-organization
  - post-write
allowed_tools:
  - run_procedure
  - vault_list
provides:
  - Pattern-Scan
summary: "## Summary|The note is a Python pattern scanner script designed to automatically identify recently modified notes and generate links between them, targeting the most recent modifications while resolvi"
tags:
  - procedure
  - procedures
---

# Note-Linker

## When to Run This

Run immediately after writing or editing a note so it gets woven into the
graph. It targets the **most recently modified note(s)** (the one(s) you
just touched) and finds (a) existing notes this note should link to, and
(b) existing notes that mention this note's title but don't link it yet.
The deterministic scan finds candidates; the small model ranks which are
genuinely related — so it's cheap enough to run after every write.

## Why This Exists

A freshly written note becomes an orphan unless it's woven into the graph.
This procedure finds existing notes the new note should link to (and notes
that mention it but don't link it). The tradeoff: it targets only the most
recently modified notes, so older orphans aren't revisited.

## Steps

### Step 1: Identify recently modified notes and gather candidate link targets

1. ```python
import json, re, os, datetime

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot-stuff" / "Memory" / "Build-Log" / "pattern-scan-latest.json")
data = json.loads(Path(out_file).read_text(encoding="utf-8"))
records = data.get("notes", [])

# Recently modified = the 3 newest by mtime (age_days ascending), excluding dailies
recent = sorted(
    (r for r in records if not r.get("is_daily")),
    key=lambda r: r.get("age_days", 9999))[:3]
recent_paths = {r["path"] for r in recent}

WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]')
by_path = {r["path"]: r for r in records}

per_note = []
for r in recent:
    path = r["path"]
    stem = r["stem"]
    existing = {l.split("#")[0].strip().lower() for l in r.get("links_out", [])}
    try:
        text = Path(vault_path, path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""
    text_lower = text.lower()

    # (a) outbound: existing notes whose TITLE appears in this note's text
    outbound = []
    for cand in records:
        cstem = cand["stem"]
        if cand["path"] == path or len(cstem) < 5 or cand.get("is_daily"):
            continue
        if cstem.lower() in existing:
            continue  # already linked
        if re.search(r'\b' + re.escape(cstem) + r'\b', text, re.IGNORECASE):
            outbound.append({"link_to": cand["path"], "title": cstem,
                             "hub": cand.get("is_hub", False),
                             "links_in": cand.get("links_in_count", 0)})
    outbound.sort(key=lambda o: -o["links_in"])

    # (b) inbound: notes that mention this note's title but don't link it
    inbound = []
    if len(stem) >= 5:
        rx = re.compile(r'(?<!\[\[)\b' + re.escape(stem) + r'\b(?!\]\])', re.IGNORECASE)
        for cand in records:
            if cand["path"] == path:
                continue
            try:
                ctext = Path(vault_path, cand["path"]).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            clinks = {l.split("#")[0].strip().lower() for l in WIKILINK_RE.findall(ctext)}
            if stem.lower() in clinks:
                continue
            if rx.search(ctext):
                inbound.append({"mention_in": cand["path"]})

    per_note.append({
        "note": path, "stem": stem, "age_days": r.get("age_days"),
        "currently_orphan": r.get("is_orphan", False),
        "outbound_suggestions": outbound[:12],
        "inbound_suggestions": inbound[:12],
    })

result = json.dumps({"recently_modified": per_note})
```

### Step 2: Pick which candidate links are genuinely related

2. [llm: For each recently-modified note in the prior step output, review the outbound and inbound link suggestions and pick ONLY the ones that are genuinely, topically related (drop false-positive matches on common words or partial overlaps). Present, per note: links to ADD inside the note (as [[Title]]), and links BACK from notes that mention it. Flag any note that is currently an orphan as highest priority. Keep suggestions concrete and ready to apply with vault_append or an edit.]

### Step 3: Validate

3. [validate: contains "link"]

## Related

- [[Pattern-Scan]] — the engine this procedure filters
- [[Find-Unlinked-Mentions]] — finds unlinked mentions this can apply
- [[Smart-Suggest-Links]] — semantic link suggestion (LLM-based)
