---
type: procedure
status: verified
baseline: true
created: 2026-07-31
description: Inventory every available tool and assess whether you have a capability gap for a specific task. Run before attempting a task.
when: Before attempting a task to see where your capabilities end
allowed_tools:
  - vault_search
summary: The note instructs users to create a `capability_audit` function that uses Python's `SelfImprover` class to scan built-in tools with custom metadata, then assesses for task-specific capability gaps an
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Capability-Audit

Inventory every available tool (built-in + meta + custom-authored) and assess whether you have a capability gap for a specific task. Returns a structured report with tool names, descriptions, and a coverage assessment.

## Why This Exists

Before attempting a task, there was no way to know whether the available tools actually cover it, leading to hitting walls mid-task. This procedure inventories every tool and assesses coverage for a specific task. The key tradeoff is that it delegates gap identification and fill proposals to the LLM rather than hardcoding a capability matrix.

## Steps

### Step 1: Inventory available tools and assess capability gaps

1. ```python
   # capability_audit is a SelfImprover method — instantiate it standalone.
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.capability_audit(task=args.get("task", ""))
   print(result)
   ```

### Step 2: Identify gaps and propose how to fill them

2. [llm: Based on the audit results, identify any capability gaps and propose how to fill them — either by creating a new tool or by using an existing procedure.]

## Related

- [[Check-Tool-Coverage]] — sibling capability assessment
- [[Write-Python-Tool]] — creates a new tool to fill a gap
- [[Build-Procedure]] — creates a procedure to fill a gap