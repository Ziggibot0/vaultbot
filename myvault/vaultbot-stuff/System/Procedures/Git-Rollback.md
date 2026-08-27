---
type: procedure
status: active
baseline: true
created: 2026-07-31
description: Restore files from git HEAD to recover from a bad self-edit.
when_to_use: When a self-edit broke something and you need to restore the original code.
allowed_tools:
  - git_rollback
summary: Git-rollback command restores files from git HEAD; requires self_improver module and session_logger for execution verification. | {self_improver}
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Git-Rollback

Restore files from git HEAD. If file_path is given, restore just that file; otherwise restore all of vaultbot_backend/.

## Why This Exists

A bad self-edit can leave the backend in a broken state. This procedure
restores files from git HEAD to recover. The tradeoff: it discards all
uncommitted changes to the target, so it is a blunt recovery tool, not a
surgical fix.

## Steps

### Step 1: Restore files from git HEAD

1. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.git_rollback(file_path=args.get("file_path", ""))
   print(result)
   ```

### Step 2: Confirm the rollback succeeded

2. [llm: Confirm the rollback succeeded. If a specific file was restored, verify it's back to the original state by reading it with code_read.]

## Related

- [[Git-Working-Diff]] — inspect uncommitted changes before deciding to roll back
- [[Safe-Write]] — safe editing that avoids the need for rollback
- [[Verify-Backend-Change]] — verify a backend change after applying it