---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: Session-start orientation for small models. Outputs the operating contract — which tools you may call, which you must never call raw, and the one rule that matters (procedures first). Deterministic, zero LLM cost. Run this at the start of every small-model session or whenever the model seems lost.
when_to_use: at the start of a session with a small/weak model, when the model is flailing (calling wrong tools, raw-editing, ignoring procedures), when a new operator puts a free/cheap model in the driver seat, whenever the model asks 'what should I do'
falsifiable_if: a small model that ran this bootstrap still calls raw write/edit tools or ignores Route-Task, meaning the contract failed to constrain behavior
applies_to:
  - small-models
  - orientation
  - routing
  - safety
allowed_tools:
  - vault_list
  - run_procedure
research_backing:
  - "[[Small-Model-Driving-Architecture]] — the design standard this bootstrap operationalizes: minimal visible toolset, procedures as the unit of work"
  - "[[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]] — deterministic scaffolding guides small models reliably"
summary: Small-Model-Bootstrap
tags:
  - procedure
  - procedures
  - small-models
---

# Small-Model-Bootstrap

## Purpose

You are a small model. That is fine — this system is designed for you. You do not need to be clever. You need to follow the contract below, exactly. The vault's procedures do the hard thinking; your job is to route to them.

This procedure outputs your **operating contract**. Read it. Obey it. When in doubt, run it again.

## The Contract (what the steps below print)

1. **You have 4 tools.** `vault_search` (find notes), `vault_read_note` (read one note by title), `execute_procedure` (run a procedure), `plan_task`/`update_task` (track multi-step work). You do not have 30 tools. If you think you need a tool you can't see, a procedure already wraps it.
2. **Procedures first, always.** Every user request goes through [[Route-Task]] first. Call `execute_procedure('Route-Task', args={'intent': '<the user request>'})`. It returns a `procedure_chain`. Then call each procedure in that chain, in order.
3. **NEVER call these raw:** `safe_write`, `code_run`, `tool_create`, `vault_delete`, `backend_restart`, `git_rollback`. They are procedure-wrapped for a reason — they can destroy the vault or the backend. If a task seems to need one, the correct procedure already exists; route to it.
4. **Never improvise a workflow.** If no procedure fits, say so and stop. Do not invent a multi-step plan from raw tools. Tell the operator: "No procedure covers this — I need a new procedure for X."
5. **One step at a time.** After every tool result, decide: is the task done? If yes, answer in plain prose. If no, call the ONE next tool. Never plan more than one tool call ahead.
6. **Answer from the vault.** Cite notes with [[wikilinks]]. If the vault is thin, route to research via Route-Task — do not make things up.

## Steps

### Step 1: Print the operating contract

1. ```python
import json
contract = {
    "you_are": "a small model driving VaultBot — the procedures think, you route",
    "your_tools": ["vault_search", "vault_read_note", "execute_procedure", "plan_task", "update_task"],
    "front_door": "Route-Task — EVERY user request goes through it first",
    "never_call_raw": ["safe_write", "code_run", "tool_create", "vault_delete", "backend_restart", "git_rollback"],
    "rule_1": "procedures first, always — call execute_procedure('Route-Task', args={'intent': request}) before anything else",
    "rule_2": "if no procedure fits, STOP and say so — never improvise with raw tools",
    "rule_3": "one step at a time — after each tool result, either answer or call exactly one tool",
    "rule_4": "answer from the vault with [[wikilinks]] — never fabricate"
}
result = json.dumps(contract, indent=2)
```

### Step 2: List available procedures so the model knows what exists

2. ```python
import json
try:
    listing = vault_list(directory="vaultbot_stuff/System/Procedures")
    files = listing if isinstance(listing, list) else json.loads(listing).get("files", [])
    names = sorted(f.replace(".md", "").split("/")[-1] for f in files)
    result = json.dumps({"available_procedures": names, "count": len(names),
                         "hint": "these are your capabilities — you never need raw tools beyond the 4 in your contract"}, indent=2)
except Exception as e:
    result = json.dumps({"available_procedures": [], "error": str(e),
                         "hint": "call Route-Task anyway — it knows the chains"})
```

### Step 3: Print the first-action instruction

3. ```python
import json
result = json.dumps({
    "bootstrap_complete": True,
    "your_first_action": "Take the user's request and call: execute_procedure('Route-Task', args={'intent': '<user request>'})",
    "then": "run each procedure in the returned procedure_chain, in order, passing the original request as args",
    "if_lost": "re-run this bootstrap: execute_procedure('Small-Model-Bootstrap')"
}, indent=2)
```

## Usage

Run at session start, or any time the model flails:

```
execute_procedure('Small-Model-Bootstrap')
```

Three deterministic code steps, zero LLM calls. The output IS the orientation — the model reads it from the tool result and follows it.

## Why this works

Small models fail when they must (a) pick from many tools, (b) remember multi-step workflows, or (c) judge safety. This bootstrap removes all three: the toolset is 4 items, the workflow is "Route-Task → chain", and safety is "never call the 6 dangerous tools raw." The intelligence lives in the procedures, per [[Small-Model-Driving-Architecture]] — the model is a router, not a reasoner.

## Falsifiability

This procedure fails if a bootstrapped small model still raw-calls dangerous tools or bypasses Route-Task. Test: give a small model a self-edit task after bootstrap and check whether it routes through [[Safe-Write]] instead of calling `safe_write` directly.
