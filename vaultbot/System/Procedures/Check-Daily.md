---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-04
description: "Granular probe: filters Vault-Walk output to identify daily notes (stem matches YYYY-MM-DD pattern). A ~10-line filter — no vault walk needed. Returns JSON list of daily note records."
when_to_use: when you need to find daily notes in the vault. Call this probe instead of re-walking the vault — it filters the Vault-Walk JSON output. The Pattern-Scan orchestrator calls this as one of its signal probes.
falsifiable_if: the output includes notes whose stem doesn't match YYYY-MM-DD, or misses daily notes that Vault-Walk returned
applies_to:
  - vault-maintenance
  - pattern-recognition
  - probe
allowed_tools:
  - run_procedure
summary: Check-Daily — granular probe that filters Vault-Walk output for notes with YYYY-MM-DD filenames. Returns JSON list of daily note records.
tags:
  - procedure
  - procedures
  - probe
---

# Check-Daily

## When to Run This

Check-Daily is a **signal probe** in the Pattern-Scan decomposition. It filters the Vault-Walk output to identify daily notes — notes whose stem (filename without `.md`) matches the `YYYY-MM-DD` date pattern. This is a pure filter: no vault walk, no file reads, just a ~10-line Python filter over the JSON that Vault-Walk already produced.

Run Check-Daily when you need to find daily notes. Do NOT re-walk the vault — call `run_procedure('Vault-Walk')` first, then pass the output to this probe.

## Why This Exists (compounding design)

The original Pattern-Scan monolith computed `is_daily` inline during its first pass. By extracting it into a standalone probe, we get:

- **Testability:** Verify daily detection independently — does it catch all YYYY-MM-DD filenames?
- **Composability:** The orchestrator runs Vault-Walk once, then feeds the output to Check-Daily (and other probes) without re-walking
- **Replaceability:** If the date pattern changes (e.g., also match YYYY-MM-DD-HHMM), only this probe changes — nothing else

## Inputs

- **vault_walk_json** (string): The JSON output from Vault-Walk — a JSON list of per-note records. Pass this as an argument when calling `execute_procedure('Check-Daily', args={'vault_walk_json': ...})`.

## Output contract

- **Return value (final step `result`):** a JSON list of daily note records (same structure as Vault-Walk records, but only those with `stem` matching `YYYY-MM-DD`).

## Steps

### Step 1: Filter Vault-Walk output for daily notes

1. ```python
import json, re

vault_walk_json = args.get('vault_walk_json', '[]')
records = json.loads(vault_walk_json) if isinstance(vault_walk_json, str) else vault_walk_json

DAILY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
daily_notes = [r for r in records if DAILY_RE.match(r.get('stem', ''))]

result = json.dumps(daily_notes)
```

### Step 2: Report headline counts

2. [llm: Report the Check-Daily headline count from the prior step output in one short sentence: how many daily notes were found out of the total. Do NOT list individual notes — this is a signal probe; the orchestrator reports specifics.]

### Step 3: Validate output

3. [validate: is a JSON list]
