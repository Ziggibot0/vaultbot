---
type: procedure
status: flagged
baseline: true
created: 2026-08-10
summary: "Router procedure for the VaultBot loop – orchestrates procedure selection based on intent and context."
description: |
  DEPRECATED (heuristic router). This procedure uses bespoke keyword mapping and
  is retained only as historical context. Do not execute it for live routing.
  Use deterministic routing and explicit procedure contracts instead.
tags:
  - router
  - loop
  - deprecated
  - legacy-heuristic
when_to_use: "never for live routing; read-only historical reference"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
allowed_tools:
  - llm_generate
  - vault_safe_write
model_cartridge: big
---

## Status

Deprecated and flagged. This procedure demonstrates the anti-pattern now
documented in AGENTS/CONTRIBUTING: bespoke keyword heuristics for behavior
policy.

Use these instead:

- [[Route-Task]] for structured intent classification
- [[Decision-Tree-Router]] for deterministic branching
- [[Flywheel-Issue-Autopilot]] for issue queue execution

## Overview
The Router operates in three phases:
1. **Analyze Intent** – use a small LLM prompt to classify the request into one of our core categories.
2. **Select Procedure** – map intent to a procedure name (e.g., `Smart-Vault-Search`, `Research-Batch`).
3. **Execute & Persist** – run the chosen procedure, capture its output, and write a summary note.

## Why This Exists

The VaultBot loop needs a single place to decide which core procedure to invoke for a given request, so it can be extended without changing the loop itself. This procedure closes that gap by centralizing decision logic into three phases: analyze intent, select procedure, execute and persist. The tradeoff is that it centralizes decision logic so new intent-to-procedure mappings can be added without modifying this file.

## Step 1: Analyze Intent
### Step 1.1: Summarize User Query
```python
query = args.get('user_query', '')
summary = llm_generate("Summarize the intent of this query in one sentence.", input=query)
args['intent_summary'] = summary.strip()
```

## Step 2: Map Intent to Procedure
### Step 2.1: Simple Keyword Mapping
```python
intent = args.get('intent_summary', '').lower()
if 'search' in intent or 'find' in intent:
    proc_name = "Smart-Vault-Search"
elif 'research' in intent or 'study' in intent:
    proc_name = "Research-Batch"
elif 'lint' in intent or 'check' in intent:
    proc_name = "Vault-Lint"
else:
    proc_name = "Unknown"  # fallback
args['selected_procedure'] = proc_name
```

## Step 3: Execute & Persist
### Step 3.1: Call the Procedure
```python
if args.get('selected_procedure') != "Unknown":
    result = execute_procedure(proc_name, args=args)
else:
    result = {"error": "No matching procedure found."}
args['procedure_result'] = result
```

### Step 3.2: Write Summary Note
```python
output_note_path = f"vaultbot/Knowledge/Router-Execution-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.md"
content = f"---\ntype: note\nstatus: completed\ncreated: {datetime.utcnow().isoformat()}\nsummary: Result of router execution for query '{args.get('user_query')}'\n---\n\n{json.dumps(args['procedure_result'], indent=2)}"
vault_safe_write(content=content, dry_run=False, file_path=output_note_path)
```

> **Links to relevant procedures:** [[Smart-Vault-Search]], [[Research-Batch]], [[Vault-Lint]]

## Related

- [[Route-Task]] — the intent classifier this router's decision logic mirrors
- [[Smart-Vault-Search]] — the search procedure this dispatches to
- [[Research-Batch]] — the research procedure this dispatches to