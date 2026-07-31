---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-31
description: "Report VaultBot's operational state: backend status, autonomous researcher state, index/graph stats, and current model. Use when the user asks what you've been doing or what you can do."
when_to_use: "when the user asks about status, health, or what VaultBot is doing"
applies_to:
  - status
  - diagnostics
allowed_tools:
  - vaultbot_status
---

# VaultBot-Status

## When to Run This

Run this when the user asks about VaultBot's status, health, what it's been doing, or what it can do.

## Steps

### Step 1: Get status

1. ```python
result = vaultbot_status() if hasattr(vaultbot_status, '__call__') else vaultbot_status.run({})
```

2. [llm: Report the status to the user in a clear, concise summary. Include: backend health, autonomous researcher state, index size, graph node count, and current model. Be natural — don't just dump JSON.]