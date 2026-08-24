---
type: procedure
status: active
baseline: true
model_cartridge: big
created: 2026-07-31
description: Safely edit backend Python source with syntax check and auto-rollback.
when_to_use: When you need to edit a .py file under vaultbot_backend/. Verifies the edit won't break the backend.
allowed_tools:
  - safe_write
  - code_read
summary: SUMMARY
tags:
  - procedure
  - procedures
falsifiable_if: the procedure produces incorrect output or fails to complete its stated task
---

# Safe-Write

Edit backend Python source code safely. The tool syntax-checks, writes as UTF-8, and for core modules imports the whole backend in a subprocess — if the import fails, the edit is rejected and the original is auto-restored.

## Why This Exists

Editing backend Python source directly can break the backend with no way to recover. This procedure closes that gap by syntax-checking, writing as UTF-8, and importing the whole backend in a subprocess for core modules — rejecting the edit and auto-restoring the original if the import fails. The tradeoff is that it is a big-cartridge procedure because the import-graph verification is expensive.

## Steps

### Step 1: Read the file to understand its current state

1. [llm: Read the file you want to edit using code_read to understand its current state.]

### Step 2: Write the Python file with syntax check and auto-rollback

2. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.safe_write(
       file_path=args["file_path"],
       content=args["content"],
       dry_run=args.get("dry_run", False)
   )
   print(result)
   ```

### Step 3: Apply the edit or fix the import error

3. [llm: If dry_run was used and passed, re-run with dry_run=False to apply. If the edit was rejected, diagnose the import error and fix the code before retrying.]

## Related

- [[Preflight-Safety-Check]] — verifies the system is healthy before editing
- [[Proc-Step-Summary]] — verifies the edit still imports cleanly
- [[Run-Test-Suite]] — the fuller verification gate after the edit