---
type: procedure
status: active
model_cartridge: big
created: 2026-07-31
description: "Execute Python code in a sandbox to test or run scripts."
when_to_use: "When you need to test code before writing it, process data, or run a quick script."
allowed_tools: [code_run]
---

# Code-Run

Execute Python code in a sandboxed subprocess. Returns stdout, stderr, and exit code.

## Steps

1. ```python
   result = code_run(code=args.get("code", ""), timeout=args.get("timeout", 15))
   print(result)
   ```

2. [llm: Analyze the output. If the code failed, diagnose the error and decide whether to fix the code or report the issue.]