---
type: procedure
description: Bare-minimum test — does llm_generate work in a code step?
allowed_tools:
  - llm_generate
model_cartridge: small
version: 1.0.0
activation: manual
status: raw
baseline: true
created: 2026-08-06
summary: Steps
tags:
  - procedure
  - procedures
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

## Why This Exists

This is the bare-minimum smoke test for the code-step execution path: does `llm_generate` actually work when called from inside a code step? It exists to isolate that single question from all other procedure machinery. The tradeoff: it's deliberately minimal — one call, no validation — so a failure here unambiguously points at the llm_generate injection rather than any other layer.

## Steps

### Step 1: Test that llm_generate works in a code step

1. ```python
result = llm_generate("Say 'hello world' and nothing else.")
```

## Related

- [[Test-RunProcedure-Return]] — inspects what run_procedure returns in a code step
- [[Test-Dispatch-DSL]] — the DSL smoke test
- [[Test-Run-DSL]] — the DSL `run` entry type test
