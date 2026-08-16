---
type: procedure
description: Test that the DSL `run` entry type correctly calls a sub-procedure and branches on its parsed output. Uses Authority-Check as the sub-procedure.
allowed_tools:
  - run_procedure
  - llm_generate
model_cartridge: small
version: 1.0.0
activation: manual
status: experimental
baseline: true
created: 2026-08-06
tags:
  - procedure
  - test
  - dsl
summary: Test-Run-DSL
---

# Test-Run-DSL

## Dispatch

- run:
    procedure: Authority-Check
    args:
      user_directive: "NO SMALL MODEL GATE — do not add any gate"
      conflicting_note: Small-Model-Driving-Architecture
      context: "I was about to add a SMALL_MODEL_MODE gate"
    output_as: authority_check

- condition:
    if: "{{ authority_check.ruling == 'USER_DIRECTIVE_WINS' }}"
    then: [done]
    else: [Gap-Fill]
    output_as: next_action

## Steps

### Step 2: Return result

2. ```python
import json
# prior_results[0] is the dispatch step's final_output (a JSON string)
dispatch_ns = prior_results[0] if prior_results else "{}"
if isinstance(dispatch_ns, str):
    dispatch_ns = json.loads(dispatch_ns)
# authority_check is the run-procedure result dict
auth = dispatch_ns.get("authority_check", {})
# next_action is the condition's chain (already a list, not a dict)
next_chain = dispatch_ns.get("next_action", [])
result = {
    "authority_ruling": auth.get("ruling", "unknown"),
    "next_action": next_chain,
    "condition_met": dispatch_ns.get("_prev", {}).get("condition_met", None),
}
```
