---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Run the vault graph analyzer to measure graph health: islands, isolated nodes, connectivity ratio. Returns metrics for downstream dream sub-procedures to act on."
when_to_use: As part of a Dream-Pass cycle, or independently to check vault graph health.
applies_to:
  - vault
  - graph
  - analysis
allowed_tools:
  - vault_graph_analyzer
falsifiable_if: it fails to return graph metrics or crashes on an empty vault
success_count: 0
failure_count: 0
success_rate: 0.0
summary: Dream-Analyze
tags:
  - procedure
  - procedures
---

# Dream-Analyze

Runs the vault graph analyzer to produce a health snapshot. The output is used by Dream-Link (to find orphans to connect) and Dream-Validate (to compare before/after).

## Step 1: Run graph analyzer

1. ```python
import json

data = vault_graph_analyzer()
result = json.dumps(data)
```