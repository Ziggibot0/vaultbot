---
type: procedure
status: active
baseline: true
model_cartridge: big
created: 2026-07-31
description: Create a new custom tool and register it for immediate use.
when_to_use: When you realize you lack a capability and need to build a new tool for yourself.
allowed_tools:
  - tool_create
  - code_run
  - code_read
summary: Write-Python-Tool
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Write-Python-Tool

Create a new tool that is written to custom_tools/ and immediately loaded/registered. You (and external MCP clients) can call it in the very next turn.

## Why This Exists

When VaultBot lacks a capability, it needs a way to build a new tool for itself rather than waiting for a human. This procedure exists to design, test, and register a new custom tool for immediate use. The key tradeoff: it tests the tool code with code_run before creating it, so a broken tool isn't registered.

## Steps

### Step 1: Design the tool and write its code

1. [llm: Think about what the tool should do. Write the Python code that defines `def run(args: dict) -> dict:`. Test it with code_run first.]

### Step 2: Test the tool code

2. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.code_run(code=args.get("test_code", "print('no test provided')"), timeout=15)
   print("Test result:", result)
   ```

### Step 3: Decide whether to proceed or fix

3. [llm: If the test passed, proceed to create the tool. If it failed, fix the code and re-test.]

### Step 4: Create and register the tool

4. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.tool_create(
       tool_name=args["tool_name"],
       description=args["description"],
       parameters=args["parameters"],
       code=args["code"]
   )
   print(result)
   ```

### Step 5: Confirm the tool was created and registered

5. [llm: Confirm the tool was created and registered. The tool is now callable in the next turn.]

## Related

- [[Write-Procedure-Draft]] — drafts a procedure note for review
- [[Capability-Audit]] — audits capabilities to find gaps
- [[Check-Tool-Coverage]] — checks tool coverage