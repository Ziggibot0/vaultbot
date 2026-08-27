---
type: procedure
status: active
baseline: true
created: 2026-08-04
description: "Granular probe: filters Vault-Walk output to identify thin notes (chars < 500). A ~10-line filter — no vault walk needed. Returns JSON list of thin note records."
when_to_use: when you need to find thin notes in the vault. Call this probe instead of re-walking the vault — it filters the Vault-Walk JSON output. The Pattern-Scan orchestrator calls this as one of its signal probes.
falsifiable_if: the output includes notes with chars >= 500, or misses notes with chars < 500 that Vault-Walk returned
applies_to:
  - vault-maintenance
  - pattern-recognition
  - probe
allowed_tools:
  - run_procedure
summary: Check-Thin — granular probe that filters Vault-Walk output for notes with chars < 500. Returns JSON list of thin note records.
tags:
  - procedure
  - procedures
  - probe
---

# Check-Thin

## When to Run This

Check-Thin is a **signal probe** in the Pattern-Scan decomposition. It filters the Vault-Walk output to identify notes that are "thin" — fewer than 500 characters. This is a pure filter: no vault walk, no file reads, just a ~10-line Python filter over the JSON that Vault-Walk already produced.

Run Check-Thin when you need to find thin notes. Do NOT re-walk the vault — call `run_procedure('Vault-Walk')` first, then pass the output to this probe.

## Why This Exists (compounding design)

The original Pattern-Scan monolith computed `is_thin` inline during its first pass. By extracting it into a standalone probe, we get:

- **Testability:** Verify thin detection independently — does it catch all notes under 500 chars?
- **Composability:** The orchestrator runs Vault-Walk once, then feeds the output to Check-Thin (and other probes) without re-walking
- **Replaceability:** If the threshold changes (e.g., 500 → 300), only this probe changes — nothing else

## Inputs

- **vault_walk_json** (string): The JSON output from Vault-Walk — a JSON list of per-note records. Pass this as an argument when calling `execute_procedure('Check-Thin', args={'vault_walk_json': ...})`.

## Output contract

- **Return value (final step `result`):** a JSON list of thin note records (same structure as Vault-Walk records, but only those with `chars < 500`).

## Steps

### Step 1: Filter Vault-Walk output for thin notes

1. ```python
import json

vault_walk_json = args.get('vault_walk_json', '[]')
records = json.loads(vault_walk_json) if isinstance(vault_walk_json, str) else vault_walk_json

THIN_THRESHOLD = 500
thin_notes = [r for r in records if r.get('chars', 0) < THIN_THRESHOLD]

result = json.dumps(thin_notes)
```

### Step 2: Report headline counts

2. [llm: Report the Check-Thin headline count from the prior step output in one short sentence: how many thin notes were found out of the total. Do NOT list individual notes — this is a signal probe; the orchestrator reports specifics.]

### Step 3: Validate output

3. [validate: is a JSON list]

## Related

- [[Vault-Walk]] — produces the JSON this probe filters
- [[Pattern-Scan]] — the orchestrator that calls this probe
- [[Check-Stub]] — sibling probe in the same decomposition
