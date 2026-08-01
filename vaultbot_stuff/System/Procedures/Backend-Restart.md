---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Restart the VaultBot backend process and reconnect."
when_to_use: "When the backend needs a restart after code changes or if it's unresponsive."
allowed_tools: [backend_restart]
---

# Backend-Restart

Restart the VaultBot backend process. Sends a WebSocket message to the Obsidian plugin to spawn a new backend process.

## Steps

1. ```python
   result = backend_restart()
   print(result)
   ```

2. [llm: The backend is restarting. Wait for it to come back up and verify it's healthy before proceeding.]