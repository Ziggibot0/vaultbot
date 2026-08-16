---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-04
description: "Granular probe: filters Vault-Walk output to identify notes with open todos (contains '- [ ]' checkboxes). Returns JSON list of note records with has_todo and todo_count fields added."
when_to_use: when you need to find notes with open todos in the vault. Call this probe instead of re-walking the vault — it filters the Vault-Walk JSON output. The Pattern-Scan orchestrator calls this as one of its signal probes.
falsifiable_if: the output includes notes without open todos, or misses notes with open todos that Vault-Walk returned
applies_to:
  - vault-maintenance
  - pattern-recognition
  - probe
allowed_tools: []
summary: Count-Todos — granular probe that filters Vault-Walk output for notes with open checkboxes (- [ ]). Returns JSON list with has_todo and todo_count fields.
tags:
  - procedure
  - procedures
  - probe
---

# Count-Todos

## When to Run This

Count-Todos is a **signal probe** in the Pattern-Scan decomposition. It filters the Vault-Walk output to identify notes that contain open todo checkboxes (`- [ ]`). This is a pure filter: no vault walk, no file reads, just a ~10-line Python filter over the JSON that Vault-Walk already produced.

Run Count-Todos when you need to find notes with open todos. Do NOT re-walk the vault — call `run_procedure('Vault-Walk')` first, then pass the output to this probe.

## Why This Exists (compounding design)

The original Pattern-Scan monolith computed `has_todo` and `todo_count` inline during its first pass. By extracting them into a standalone probe, we get:

- **Testability:** Verify todo detection independently — does it catch all notes with `- [ ]`?
- **Composability:** The orchestrator runs Vault-Walk once, then feeds the output to Count-Todos (and other probes) without re-walking
- **Replaceability:** If the todo pattern changes (e.g., also match `* [ ]`), only this probe changes — nothing else

## Inputs

- **vault_walk_json** (string): The JSON output from Vault-Walk — a JSON list of per-note records. Must include the `body_lower` field (Vault-Walk v2+). Pass this as an argument when calling `execute_procedure('Count-Todos', args={'vault_walk_json': ...})`.

## Output contract

- **Return value (final step `result`):** a JSON list of note records with `has_todo` (bool) and `todo_count` (int) fields added. Only notes with `has_todo: true` are included.

## Steps

### Step 1: Filter Vault-Walk output for notes with open todos

1. ```python
import json

vault_walk_json = args.get('vault_walk_json', '[]')
records = json.loads(vault_walk_json) if isinstance(vault_walk_json, str) else vault_walk_json

todo_notes = []
for r in records:
    body = r.get('body_lower', '')
    count = body.count('- [ ]')
    if count > 0:
        r['has_todo'] = True
        r['todo_count'] = count
        todo_notes.append(r)

result = json.dumps(todo_notes)
```

### Step 2: Report headline counts

2. [llm: Report the Count-Todos headline counts from the prior step output in one short sentence: how many notes have open todos, and the total number of open todos across all notes. Do NOT list individual notes — this is a signal probe; the orchestrator reports specifics.]

### Step 3: Validate output

3. [validate: is a JSON list]
