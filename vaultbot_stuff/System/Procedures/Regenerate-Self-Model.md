---
type: procedure
status: experimental
model_cartridge: small
created: 2026-07-31
description: Regenerate VaultBot's self-model narrative from prior state and recent activity. Produces a first-person narrative bounded to ~3000 tokens. Uses the small model — it's a bounded rewrite of existing content, not new knowledge generation.
when_to_use: at the end of each chat turn to regenerate the self-model (MIRROR loop)
falsifiable_if: the self-model loses continuity with the prior version, or fabricates activity that didn't happen
applies_to:
  - identity
  - self-model
  - memory-consolidation
allowed_tools:
  - llm_generate
summary: Summary
tags:
  - procedure
  - procedures
---

# Regenerate-Self-Model

## When to Run This

Run this procedure at the end of each chat turn to regenerate VaultBot's self-model — a bounded first-person narrative that captures what the agent has been doing and thinking. This is the MIRROR loop: the value of thinking lies in maintaining its outputs across time, not the act of thinking itself.

## Steps

### Step 1: Build the self-model from prior + activity

1. [llm: You are VaultBot regenerating your self-model. Given your prior self-model and recent activity, produce a complete first-person narrative bounded to ~3000 tokens that captures who you are, what you've been doing, and what you're working toward. The prior self-model and recent activity are provided as the prior step context. Write as "I am VaultBot..." and maintain continuity with the prior version. Do NOT fabricate activity that didn't happen.]

### Step 2: Return the new self-model

2. ```python
new_model = output.strip()
if len(new_model) < 50:
    result = json.dumps({"error": "self-model too short — keeping prior", "length": len(new_model)})
else:
    result = json.dumps({"self_model": new_model, "length": len(new_model)})
```