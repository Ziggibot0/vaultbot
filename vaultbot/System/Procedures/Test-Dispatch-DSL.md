---
type: procedure
description: Smoke test for the YAML Dispatch DSL — classifies intent, dispatches to chain, calls a tool, and branches on the result.
allowed_tools:
  - llm_generate
  - vault_gaps
model_cartridge: small
version: 1.1.0
activation: manual
status: raw
baseline: true
created: 2026-08-06
summary: "YAML DSL smoke test: classify → dispatch → call tool → condition on result (dotted field + _prev)"
tags:
  - procedure
  - procedures
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Test-Dispatch-DSL

## Dispatch

- classify:
    prompt: |
      Classify this request. Reply with ONLY the category word.
      Categories: research, vault-maintenance, self-improvement, unknown
      Request: {{ intent }}
    model: small
    output_as: category

- dispatch:
    on_field: "{{ category }}"
    branches:
      research: [Research-Batch, Cross-Check-Claims]
      vault-maintenance: [Dream-Pass]
      self-improvement: [Discover-Procedures]
    default: [Small-Model-Route]
    output_as: chain

- call:
    tool: vault_gaps
    output_as: gaps_data

- condition:
    if: "{{ gaps_data.count > 0 }}"
    then: [Gap-Fill]
    else: [done]
    output_as: gap_decision

- condition:
    if: "{{ _prev.chain | length > 0 }}"
    then: [execute-chain]
    else: [done]
    output_as: final_decision

## Steps

### Step 2: Return result

2. ```python
import json
# The dispatch pipeline exports result = dict(_dispatch_ns), which
# becomes prior_results[0].  Extract the fields we care about.
dispatch_ns = prior_results[0] if prior_results else {}
if isinstance(dispatch_ns, str):
    dispatch_ns = json.loads(dispatch_ns)
result = {
    "category": dispatch_ns.get("category", ""),
    "chain": dispatch_ns.get("chain", []),
    "gap_decision": dispatch_ns.get("gap_decision", []),
    "final_decision": dispatch_ns.get("final_decision", []),
    "gaps_count": dispatch_ns.get("gaps_data", {}).get("count", 0),
}
```
