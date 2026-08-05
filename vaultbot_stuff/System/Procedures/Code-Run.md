---
type: procedure
status: experimental
model_cartridge: big
created: 2026-07-31
description: Execute Python code in a sandbox to test or run scripts.
when_to_use: When you need to test code before writing it, process data, or run a quick script.
allowed_tools:
  - code_run
last_reviewed: 2026-08-03
success_count: 0
failure_count: 0
success_rate: 0.0
summary: ```text|python sandbox subprocess execution analysis | debugging strategy for failed outputs
```
|tag1,tag2,tag3|execute-python-code-sandboxed-subprocess-diagnose-error-fix-issues-llm-analyze-output-if-failed|llm: Analyze output. If failed, diagnose error and decide whether to fix cod"
tags:
  - procedure
  - procedures
---

# Code-Run

Execute Python code in a sandboxed subprocess. Returns stdout, stderr, and exit code.

## Steps

1. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.code_run(code=args.get("code", ""), timeout=args.get("timeout", 15))
   print(result)
   ```

2. [llm: Analyze the output. If the code failed, diagnose the error and decide whether to fix the code or report the issue.]