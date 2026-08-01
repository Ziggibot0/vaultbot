---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Report VaultBot's operational state and autonomous research history."
when_to_use: "When the user asks what you've been doing or what you can do."
allowed_tools: [vaultbot_status]
---

# System-Status

Report VaultBot's operational state: whether the backend and autonomous background researcher are running, and recent autonomous research history.

## Steps

1. ```python
   result = vaultbot_status()
   print(result)
   ```

2. [llm: Summarize the status for the user in a concise report.]