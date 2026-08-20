---
type: procedure
status: verified
baseline: true
model_cartridge: big
created: 2026-07-31
description: Reflect on a topic and propose 1-3 new tool abilities you could create for yourself. Use when you realize you lack an ability.
when: When you hit a wall and need to propose new capabilities
allowed_tools:
  - vault_search
  - code_read
summary: Self-reflect tool proposal for self-improver session logging with code sketch instantiation.
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Self-Reflect

Reflect on a topic and propose 1-3 new tool abilities you could create for yourself. Returns concrete proposals with code sketches you can then implement with tool_create.

## Steps

### Step 1: Reflect and propose new tool abilities

1. ```python
   # self_reflect is a SelfImprover method — instantiate it standalone.
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.self_reflect(topic=args.get("topic", ""), vault_context=args.get("vault_context", ""))
   print(result)
   ```

### Step 2: Identify which abilities to implement first

2. [llm: Based on the reflection results, identify which proposed abilities are most valuable and should be implemented first.]