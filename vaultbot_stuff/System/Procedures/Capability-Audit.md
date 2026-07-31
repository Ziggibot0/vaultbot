---
type: procedure
status: verified
created: 2026-07-31
description: "Inventory every available tool and assess whether you have a capability gap for a specific task. Run before attempting a task."
when: "Before attempting a task to see where your capabilities end"
allowed_tools: [vault_search]
---

# Capability-Audit

Inventory every available tool (built-in + meta + custom-authored) and assess whether you have a capability gap for a specific task. Returns a structured report with tool names, descriptions, and a coverage assessment.

## Steps

1. ```python
   # Call the capability_audit tool's run() function
   from custom_tools.capability_audit import run as _audit
   result = _audit({"task": args.get("task", "")})
   print(result)
   ```

2. [llm: Based on the audit results, identify any capability gaps and propose how to fill them — either by creating a new tool or by using an existing procedure.]