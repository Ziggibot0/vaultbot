---
type: procedure
status: active
model_cartridge: small
created: 2026-08-04
description: "Granular probe: filters Vault-Walk output to identify procedure notes (frontmatter type: procedure). A ~10-line filter — no vault walk needed. Returns JSON list of procedure note records."
when_to_use: when you need to find procedure notes in the vault. Call this probe instead of re-walking the vault — it filters the Vault-Walk JSON output. The Pattern-Scan orchestrator calls this as one of its signal probes.
falsifiable_if: the output includes notes whose type is not 'procedure', or misses procedure notes that Vault-Walk returned
applies_to:
  - vault-maintenance
  - pattern-recognition
  - probe
allowed_tools: []
summary: Check-Procedure-Type — granular probe that filters Vault-Walk output for notes with frontmatter type: procedure. Returns JSON list of procedure note records.
tags:
  - procedure
  - procedures
  - probe
---

# Check-Procedure-Type

## When to Run This

Check-Procedure-Type is a **signal probe** in the Pattern-Scan decomposition. It filters the Vault-Walk output to identify procedure notes — notes whose frontmatter `type` field is `"procedure"`. This is a pure filter: no vault walk, no file reads, just a ~10-line Python filter over the JSON that Vault-Walk already produced.

Run Check-Procedure-Type when you need to find procedure notes. Do NOT re-walk the vault — call `run_procedure('Vault-Walk')` first, then pass the output to this probe.

## Why This Exists (compounding design)

The original Pattern-Scan monolith computed `is_procedure` inline during its first pass. By extracting it into a standalone probe, we get:

- **Testability:** Verify procedure detection independently — does it catch all notes with `type: procedure`?
- **Composability:** The orchestrator runs Vault-Walk once, then feeds the output to Check-Procedure-Type (and other probes) without re-walking
- **Replaceability:** If the type field changes (e.g., also match `type: tool`), only this probe changes — nothing else

## Inputs

- **vault_walk_json** (string): The JSON output from Vault-Walk — a JSON list of per-note records. Pass this as an argument when calling `execute_procedure('Check-Procedure-Type', args={'vault_walk_json': ...})`.

## Output contract

- **Return value (final step `result`):** a JSON list of procedure note records (same structure as Vault-Walk records, but only those with `type == "procedure"`).

## Steps

### Step 1: Filter Vault-Walk output for procedure notes

1. ```python
import json

vault_walk_json = args.get('vault_walk_json', '[]')
records = json.loads(vault_walk_json) if isinstance(vault_walk_json, str) else vault_walk_json

procedure_notes = [r for r in records if r.get('type', '').lower() == 'procedure']

result = json.dumps(procedure_notes)
```

### Step 2: Report headline counts

2. [llm: Report the Check-Procedure-Type headline count from the prior step output in one short sentence: how many procedure notes were found out of the total. Do NOT list individual notes — this is a signal probe; the orchestrator reports specifics.]

### Step 3: Validate output

3. [validate: is a JSON list]
