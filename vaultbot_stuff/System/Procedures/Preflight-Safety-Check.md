---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-31
description: Pre-flight safety check before self-modifying operations. Verifies git clean state, critical files exist, identity files intact, disk space adequate, custom tools import cleanly, and vault directory is accessible.
when: Before any code_write or tool_create operation to verify the system is healthy enough to safely edit
allowed_tools:
  - code_read
summary: "Summary: A security audit checklist for safe self-modifying code operations to validate git cleanliness, backend files exist, and vault accessibility. Tags|safe_preflight_safety_check_git_vault_backen"
tags:
  - procedure
  - procedures
---

# Preflight-Safety-Check

Pre-flight safety check before self-modifying operations. Verifies git clean state (for rollback safety), critical backend files exist, identity files intact, disk space adequate, custom tools still import cleanly, and vault directory is accessible. Returns PASS / WARN / BLOCK with full details.

## Steps

1. ```python
   # Call the preflight_safety_check tool's run() function
   from custom_tools.preflight_safety_check import run as _preflight
   result = _preflight({})
   print(result)
   ```