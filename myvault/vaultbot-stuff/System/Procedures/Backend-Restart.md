---
type: procedure
status: active
baseline: true
created: 2026-07-31
description: Restart the VaultBot backend process and reconnect.
when_to_use: When the backend needs a restart after code changes or if it's unresponsive.
allowed_tools:
  - backend_restart
summary: Backend-Restart
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Backend-Restart

Restart the VaultBot backend process. Sends a WebSocket message to the Obsidian plugin to spawn a new backend process.

## Why This Exists

After code changes or when the backend becomes unresponsive, it must be
restarted to pick up the new state. This procedure sends a WebSocket
message to the Obsidian plugin to spawn a fresh backend process. The key
tradeoff is that it delegates the actual restart to the `backend_restart`
tool rather than reimplementing process management.

## Steps

### Step 1: Restart the backend process

1. ```python
   from custom_tools.backend_restart import run as _restart
   result = _restart({})
   print(result)
   ```

### Step 2: Wait for the backend to come back up

2. [llm: The backend is restarting. Wait for it to come back up and verify it's healthy before proceeding.]

## Related

- [[System-Status]] — checks backend health after restart
- [[Plugin-Reload]] — reloads the Obsidian plugin side
- [[Verify-Backend-Change]] — verifies a change took effect after restart