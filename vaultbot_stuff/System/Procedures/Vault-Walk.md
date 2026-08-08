---
type: procedure
status: active
model_cartridge: small
created: 2026-08-04
description: "Granular probe: walks every .md note in the vault and computes ONLY independent per-note signals (path, stem, dir, chars, has_frontmatter, type, links_out, links_out_count, age_days). No vault-wide knowledge needed — this is the first pass of Pattern-Scan extracted into its own reusable procedure. Returns raw per-note data as JSON for consumption by signal probes and the Pattern-Scan orchestrator."
when_to_use: when any procedure needs raw per-note data from the entire vault — do NOT inline vault_list() + file reads; call run_procedure('Vault-Walk') and consume the JSON output. This is the foundation layer that all signal probes (is_thin, is_stub, is_daily, etc.) filter over.
falsifiable_if: the output JSON is missing notes that vault_list() returns, or any per-note field is computed incorrectly compared to a manual check
applies_to:
  - vault-maintenance
  - pattern-recognition
  - meta-procedure
  - probe
allowed_tools:
  - vault_list
summary: Vault-Walk — granular probe that reads every .md note and returns raw per-note data (path, stem, dir, chars, frontmatter, type, links_out, age_days). Foundation layer for all signal probes.
tags:
  - procedure
  - procedures
  - probe
---

# Vault-Walk

## When to Run This

Vault-Walk is the **foundation layer** of the Pattern-Scan decomposition. It walks every `.md` note in the vault and computes only the signals that are **independent per-note** — no vault-wide knowledge required. This is the first pass of the original Pattern-Scan monolith, extracted into its own reusable procedure.

Run Vault-Walk when you need raw per-note data from the entire vault. Do NOT inline `vault_list()` + file reads in other procedures — call `run_procedure('Vault-Walk')` and consume the JSON output. Every signal probe (is_thin, is_stub, is_daily, etc.) is a ~10-line filter over this output.

## Why This Exists (compounding design)

The original Pattern-Scan was a 213-line monolith with two passes: one for independent per-note signals, one for vault-wide resolution (backlinks, broken links, orphans). By extracting the first pass into Vault-Walk, we get:

- **Reusability:** Any procedure can get raw per-note data without re-walking the vault
- **Testability:** Vault-Walk can be verified independently — does it return the right data for every note?
- **Composability:** Signal probes filter Vault-Walk output; the orchestrator runs Vault-Walk once and feeds it to all probes

## Inputs

None — Vault-Walk reads the entire vault via `vault_list()`.

## Output contract

- **Return value (final step `result`):** a JSON list of per-note records. Each record has these fields:

| field | meaning |
|---|---|
| `path` | vault-relative path (forward slashes) |
| `stem` | note title without `.md` |
| `dir` | top-level folder |
| `chars` | file size in characters |
| `has_frontmatter` | bool — does the file start with `---`? |
| `type` | frontmatter `type:` value ("" if none) |
| `links_out` | list of wikilink targets (stems) |
| `links_out_count` | int — length of links_out |
| `age_days` | days since file mtime (float, rounded to 1 decimal) |
| `body_lower` | full note body lowercased (for text-matching probes like Check-Stub, Count-Todos) |

## Steps

### Step 1: Walk every note and compute independent per-note signals

1. ```python
import os, re, json, datetime
from pathlib import Path

vault = str(Path(vault_path).resolve())

WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]')
FM_TYPE_RE = re.compile(r'^type:\s*(.+?)\s*$', re.MULTILINE)

now = datetime.datetime.now().timestamp()
paths = vault_list()  # absolute .md paths, ignored dirs already excluded

records = []
for ap in paths:
    try:
        rel = os.path.relpath(ap, vault).replace("\\", "/")
        text = Path(ap).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue

    stem = rel.split("/")[-1][:-3]  # strip .md
    top_dir = rel.split("/")[0] if "/" in rel else "(root)"

    has_fm = text.startswith("---")
    fm_type = ""
    if has_fm:
        # Search for type: in the frontmatter block (between first two --- lines)
        end_of_fm = text.find("\n---", 3)
        search_end = end_of_fm if end_of_fm != -1 else 2000
        m = FM_TYPE_RE.search(text[:search_end])
        fm_type = (m.group(1).strip() if m else "")

    links_out = [l.strip() for l in WIKILINK_RE.findall(text)]

    try:
        age_days = (now - os.path.getmtime(ap)) / 86400.0
    except Exception:
        age_days = 0.0

    rec = {
        "path": rel,
        "stem": stem,
        "dir": top_dir,
        "chars": len(text),
        "has_frontmatter": has_fm,
        "type": fm_type,
        "links_out": links_out,
        "links_out_count": len(links_out),
        "age_days": round(age_days, 1),
        "body_lower": text.lower(),
    }
    records.append(rec)

result = json.dumps(records)
```

### Step 2: Confirm the walk completed and report headline counts

2. [llm: Report the Vault-Walk headline counts from the prior step output in one short paragraph: total notes walked, and the count of notes with frontmatter, notes with no frontmatter, and notes with at least one outgoing link. Do NOT list individual notes — this is the foundation layer only; signal probes report specifics. Note that the output is a JSON list ready for consumption by signal probes and the Pattern-Scan orchestrator.]

### Step 3: Validate output

3. [validate: is a JSON list]
