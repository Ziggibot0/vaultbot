---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: "Score the procedure library: classify every procedure as healthy/degraded/broken and surface which need review, cartridge demotion, or retirement. Calls Procedure-Eval."
when_to_use: As the final step of a Dream-Pass cycle, or standalone when the procedure library needs scoring.
falsifiable_if: it fails to classify any procedure or crashes on any step
applies_to:
  - procedures
  - evaluation
  - maintenance
allowed_tools:
  - run_procedure
  - llm_generate
summary: Dream-Evaluate
tags:
  - procedure
  - procedures
---

# Dream-Evaluate

Scores the procedure library by calling [[Procedure-Eval]]. Each procedure gets classified as healthy, degraded, or broken based on success/failure counters.

## Why This Exists

A procedure library accumulates procedures that silently degrade or break, and without scoring they never get reviewed, demoted, or retired. This procedure exists to classify every procedure as healthy/degraded/broken from its success/failure counters. The key tradeoff is that it delegates the actual scoring to [[Procedure-Eval]] rather than reimplementing it, keeping this a thin wrapper.

## Steps

### Step 1: Run Procedure-Eval and summarize

1. ```python
import json

eval_result = run_procedure("Procedure-Eval")
summary = {}
try:
    summary = json.loads(eval_result.get("final_output", "{}"))
except Exception:
    summary = {"raw": eval_result.get("final_output", "")}

result = json.dumps({
    "status": "evaluated",
    "procedure_scores": summary,
}, indent=2)
```

## Related

- [[Procedure-Eval]] — the scoring engine this delegates to
- [[Dream-Pass]] — the orchestrator that calls this as its final step
- [[Dream-When-To-Use-Update]] — runs after this to fix discoverability of poorly-scored procedures