---
type: procedure
status: active
model_cartridge: big
created: 2026-07-31
description: "Safely edit backend Python source with syntax check and auto-rollback."
when_to_use: "When you need to edit a .py file under vaultbot_backend/. Verifies the edit won't break the backend."
allowed_tools: [safe_write, code_read]
---

# Safe-Write

Edit backend Python source code safely. The tool syntax-checks, writes as UTF-8, and for core modules imports the whole backend in a subprocess — if the import fails, the edit is rejected and the original is auto-restored.

## Steps

1. [llm: Read the file you want to edit using code_read to understand its current state.]

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

3. [llm: If dry_run was used and passed, re-run with dry_run=False to apply. If the edit was rejected, diagnose the import error and fix the code before retrying.]