---
type: procedure
status: active
baseline: true
created: 2026-08-04
description: "Granular probe: filters Vault-Walk output to identify stale notes (age_days > 30). A ~10-line filter — no vault walk needed. Returns JSON list of stale note records."
when_to_use: when you need to find stale notes in the vault. Call this probe instead of re-walking the vault — it filters the Vault-Walk JSON output. The Pattern-Scan orchestrator calls this as one of its signal probes.
falsifiable_if: the output includes notes with age_days <= 30, or misses notes with age_days > 30 that Vault-Walk returned
applies_to:
  - vault-maintenance
  - pattern-recognition
  - probe
allowed_tools:
  - run_procedure
summary: Check-Staleness — granular probe that filters Vault-Walk output for notes older than 30 days. Returns JSON list of stale note records.
tags:
  - procedure
  - procedures
  - probe
---

# Check-Staleness

## When to Run This

Check-Staleness is a **signal probe** in the Pattern-Scan decomposition. It filters the Vault-Walk output to identify stale notes — notes whose `age_days` exceeds 30 days. This is a pure filter: no vault walk, no file reads, just a ~10-line Python filter over the JSON that Vault-Walk already produced.

Run Check-Staleness when you need to find stale notes. Do NOT re-walk the vault — call `run_procedure('Vault-Walk')` first, then pass the output to this probe.

## Why This Exists (compounding design)

The original Pattern-Scan monolith computed `is_stale` inline during its first pass. By extracting it into a standalone probe, we get:

- **Testability:** Verify staleness detection independently — does it catch all notes over 30 days?
- **Composability:** The orchestrator runs Vault-Walk once, then feeds the output to Check-Staleness (and other probes) without re-walking
- **Replaceability:** If the threshold changes (e.g., 30 → 90 days), only this probe changes — nothing else

## Inputs

- **vault_walk_json** (string): The JSON output from Vault-Walk — a JSON list of per-note records. Pass this as an argument when calling `execute_procedure('Check-Staleness', args={'vault_walk_json': ...})`.

## Output contract

- **Return value (final step `result`):** a JSON list of stale note records (same structure as Vault-Walk records, but only those with `age_days > 30`).

## Steps

### Step 1: Filter Vault-Walk output for stale notes

1. ```python
import json

vault_walk_json = args.get('vault_walk_json', '[]')
records = json.loads(vault_walk_json) if isinstance(vault_walk_json, str) else vault_walk_json

STALE_DAYS = 30
stale_notes = [r for r in records if r.get('age_days', 0) > STALE_DAYS]

result = json.dumps(stale_notes)
```

### Step 2: Report headline counts

2. [llm: Report the Check-Staleness headline count from the prior step output in one short sentence: how many stale notes were found out of the total. Do NOT list individual notes — this is a signal probe; the orchestrator reports specifics.]

### Step 3: Validate output

3. [validate: is a JSON list]

## Related

- [[Vault-Walk]] — produces the JSON this probe filters
- [[Pattern-Scan]] — the orchestrator that calls this probe
- [[Check-Daily]] — sibling probe in the same decomposition
