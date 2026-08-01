---
type: procedure
status: experimental
created: 2026-07-31
description: "Fix indentation errors in chat_handler.py after Remove-All-Stops procedure."
when_to_use: "after running Remove-All-Stops if chat_handler.py has indentation errors from the edits"
allowed_tools: []
spec_version: 2
success_count: 0
failure_count: 0
---

## Steps

1. ```python
   import os
   filepath = r"C:\Users\skell\Desktop\Vault2\vaultbot_stuff\vaultbot_backend\chat_handler.py"
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