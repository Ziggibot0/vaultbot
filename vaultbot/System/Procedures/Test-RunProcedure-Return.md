---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-04
description: Debug test to inspect what run_procedure returns inside a code step
summary: "Step 1: Inspect run_procedure return value"
tags:
  - procedure
  - procedures
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
allowed_tools:
  - run_procedure
---

## Why This Exists

It was unclear what shape `run_procedure` returns inside a code step — a dict, a JSON string, or something else — which made downstream parsing fragile. This debug test exists to inspect the return value's type, keys, and repr. The tradeoff: it's a one-shot diagnostic, not a reusable procedure, so it hardcodes Dream-Scan as the target.

## Step 1: Inspect run_procedure return value

1. ```python
import json

result = run_procedure("Dream-Scan")
result_type = str(type(result))
result_keys = str(list(result.keys()) if isinstance(result, dict) else "NOT A DICT")
result_repr = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)

output = json.dumps({
    "type": result_type,
    "keys": result_keys,
    "repr": result_repr[:500]
})
```

## Related

- [[Test-Procedure-Until-Pass]] — the automated test→fix→retest loop
- [[Test-LlmGenerate-Bare]] — the bare llm_generate smoke test
- [[Test-Dispatch-DSL]] — the DSL smoke test