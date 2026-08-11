---
type: procedure
status: active
model_cartridge: small
created: 2026-08-04
description: "Granular probe: filters Vault-Walk output to identify stub notes (body contains stub markers like 'TODO', 'stub', 'placeholder', 'expand', 'tbd', 'wip'). A ~10-line filter — no vault walk needed. Returns JSON list of stub note records."
when_to_use: when you need to find stub notes in the vault. Call this probe instead of re-walking the vault — it filters the Vault-Walk JSON output. The Pattern-Scan orchestrator calls this as one of its signal probes.
falsifiable_if: the output includes notes without stub markers, or misses notes with stub markers that Vault-Walk returned
applies_to:
  - vault-maintenance
  - pattern-recognition
  - probe
allowed_tools:
  - run_procedure
summary: Check-Stub — granular probe that filters Vault-Walk output for notes with stub markers (TODO, stub, placeholder, expand, tbd, wip). Returns JSON list of stub note records.
tags:
  - procedure
  - procedures
  - probe
---

# Check-Stub

## When to Run This

Check-Stub is a **signal probe** in the Pattern-Scan decomposition. It filters the Vault-Walk output to identify notes that contain stub markers — keywords like "TODO", "stub", "placeholder", "expand", "tbd", or "wip" in the body text. This is a pure filter: no vault walk, no file reads, just a ~10-line Python filter over the JSON that Vault-Walk already produced.

Run Check-Stub when you need to find stub notes. Do NOT re-walk the vault — call `run_procedure('Vault-Walk')` first, then pass the output to this probe.

## Why This Exists (compounding design)

The original Pattern-Scan monolith computed `is_stub` inline during its first pass. By extracting it into a standalone probe, we get:

- **Testability:** Verify stub detection independently — does it catch all notes with stub markers?
- **Composability:** The orchestrator runs Vault-Walk once, then feeds the output to Check-Stub (and other probes) without re-walking
- **Replaceability:** If the stub markers change (e.g., add "draft"), only this probe changes — nothing else

## Inputs

- **vault_walk_json** (string): The JSON output from Vault-Walk — a JSON list of per-note records. Must include the `body_lower` field (Vault-Walk v2+). Pass this as an argument when calling `execute_procedure('Check-Stub', args={'vault_walk_json': ...})`.

## Output contract

- **Return value (final step `result`):** a JSON list of stub note records (same structure as Vault-Walk records, but only those with stub markers in body_lower).

## Steps

### Step 1: Filter Vault-Walk output for stub notes

1. ```python
import json

vault_walk_json = args.get('vault_walk_json', '[]')
records = json.loads(vault_walk_json) if isinstance(vault_walk_json, str) else vault_walk_json

STUB_MARKERS = ("todo", "stub", "placeholder", "expand me", "tbd", "wip")
stub_notes = [r for r in records if any(sm in r.get('body_lower', '') for sm in STUB_MARKERS)]

result = json.dumps(stub_notes)
```

### Step 2: Report headline counts

2. [llm: Report the Check-Stub headline count from the prior step output in one short sentence: how many stub notes were found out of the total. Do NOT list individual notes — this is a signal probe; the orchestrator reports specifics.]

### Step 3: Validate output

3. [validate: is a JSON list]
