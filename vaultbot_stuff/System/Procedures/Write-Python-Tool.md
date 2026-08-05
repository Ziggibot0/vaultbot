---
type: procedure
status: active
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
---

# Write-Python-Tool

Create a new tool that is written to custom_tools/ and immediately loaded/registered. You (and external MCP clients) can call it in the very next turn.

## Steps

1. [llm: Think about what the tool should do. Write the Python code that defines `def run(args: dict) -> dict:`. Test it with code_run first.]

2. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.code_run(code=args.get("test_code", "print('no test provided')"), timeout=15)
   print("Test result:", result)
   ```

3. [llm: If the test passed, proceed to create the tool. If it failed, fix the code and re-test.]

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

5. [llm: Confirm the tool was created and registered. The tool is now callable in the next turn.]