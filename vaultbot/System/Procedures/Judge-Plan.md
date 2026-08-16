---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Judge whether a plan has been completed. Given a plan's goal and subtask results, returns whether the plan is complete, reasoning, and any missing subtasks. Uses the small model — judging completion is simple verification.
when_to_use: when a plan has been executed and you need to judge whether it's complete
falsifiable_if: the judge declares a plan complete when subtasks are not done, or incomplete when all are done
applies_to:
  - plan-execution
  - task-management
  - verification
allowed_tools:
  - llm_generate
summary: Analyze this note and write a one-sentence summary (max 120 chars) describing what the note SAYS, not just its title. Use a verb. Output ONLY the summary, nothing else.
tags:
  - procedure
  - procedures
last_reviewed: 2026-08-15
---

# Judge-Plan

## When to Run This

Run this procedure when a plan has been executed and you need to judge whether it's complete. The judge reads the plan's goal and each subtask's result + verifier status, then determines whether the overall goal has been achieved.

## Steps

### Step 1: Ask the small model to judge the plan

1. [llm: You are a strict judge evaluating whether a plan has been completed. Given the plan's goal and the results of each subtask, determine if the plan is complete. Return JSON: {"complete": true/false, "reasoning": "...", "missing": ["subtask_id", ...]}. The plan details are provided as the prior step context. Return ONLY the JSON.]

### Step 2: Return the judgment

2. ```python
import json
try:
    start = output.find("{")
    end = output.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(output[start:end+1])
    else:
        parsed = {"complete": False, "reasoning": "could not parse judge response", "missing": []}
except Exception:
    parsed = {"complete": False, "reasoning": "judge error", "missing": []}
result = json.dumps(parsed)
```