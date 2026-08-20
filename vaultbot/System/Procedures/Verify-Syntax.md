---
type: procedure
status: active
baseline: true
created: 2026-07-31
description: Verify Python file syntax before restarting backend.
when_to_use: before restarting the backend after editing Python files
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
model_cartridge: small
---

## Steps

### Step 1: Verify chat_handler.py compiles without errors

1. ```python
   import py_compile
   import sys
   filepath = r"C:\Users\skell\Desktop\Vault2\vaultbot\vaultbot_backend\chat_handler.py"
   try:
       py_compile.compile(filepath, doraise=True)
       print("VALID: chat_handler.py compiles without errors")
   except py_compile.PyCompileError as e:
       print(f"SYNTAX ERROR: {e}")
       sys.exit(1)
   ```