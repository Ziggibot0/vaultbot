---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Restore files from git HEAD to recover from a bad self-edit.
when_to_use: When a self-edit broke something and you need to restore the original code.
allowed_tools:
  - git_rollback
summary: Git-rollback command restores files from git HEAD; requires self_improver module and session_logger for execution verification. | {self_improver}
tags:
  - procedure
  - procedures
---

# Git-Rollback

Restore files from git HEAD. If file_path is given, restore just that file; otherwise restore all of vaultbot_backend/.

## Steps

1. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.git_rollback(file_path=args.get("file_path", ""))
   print(result)
   ```

2. [llm: Confirm the rollback succeeded. If a specific file was restored, verify it's back to the original state by reading it with code_read.]