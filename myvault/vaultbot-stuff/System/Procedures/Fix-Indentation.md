---
type: procedure
status: active
baseline: true
created: 2026-07-31
description: Fix indentation errors in chat_handler.py after Remove-All-Stops procedure.
when_to_use: after running Remove-All-Stops if chat_handler.py has indentation errors from the edits
allowed_tools: []
spec_version: 2
success_count: 0
failure_count: 0
summary: Steps
tags:
  - procedure
  - procedures
last_reviewed: 2026-08-15
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

## Why This Exists

Running Remove-All-Stops can leave `chat_handler.py` with indentation
errors from the edits. This procedure fixes the specific known indentation
regression (the `_MAX_READ_ONLY_STREAK` line) and verifies syntax. The
tradeoff: it targets one hardcoded pattern, so a differently-formatted
regression won't be caught.

## Steps

### Step 1: Fix the indentation error in chat_handler.py

1. ```python
   import os
   filepath = r"C:\Users\skell\Desktop\Vault2\vaultbot\vaultbot_backend\chat_handler.py"
   with open(filepath, "r", encoding="utf-8") as f:
       content = f.read()
   
   # Fix the indentation error: _MAX_READ_ONLY_STREAK line has 12 spaces instead of 8
   old = "            _MAX_READ_ONLY_STREAK = 999999  # DISABLED by Remove-All-Stops procedure"
   new = "        _MAX_READ_ONLY_STREAK = 999999  # DISABLED by Remove-All-Stops procedure"
   
   if old in content:
       content = content.replace(old, new)
       with open(filepath, "w", encoding="utf-8") as f:
           f.write(content)
       print("SUCCESS: Fixed indentation for _MAX_READ_ONLY_STREAK")
   else:
       print("NOTICE: Pattern not found - may already be fixed or different format")
   
   # Verify syntax
   import py_compile
   try:
       py_compile.compile(filepath, doraise=True)
       print("SYNTAX OK")
   except py_compile.PyCompileError as e:
       print(f"SYNTAX ERROR: {e}")
   ```

## Related

- [[Remove-All-Stops]] — the procedure whose edits cause the indentation regression
- [[Verify-Syntax]] — syntax verification for backend changes
- [[Safe-Write]] — safe file editing with rollback