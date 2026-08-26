---
type: procedure
status: experimental
baseline: true
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

## Why This Exists

Graph health (islands, isolated nodes, connectivity ratio) must be measured before any linking or validation work can happen, and doing it by hand is error-prone. This procedure exists to run the vault graph analyzer and return a metrics snapshot for downstream dream sub-procedures to act on. The key tradeoff is that it is a pure measurement step — it produces data, not changes.

## Step 1: Run graph analyzer

1. ```python
import json

data = vault_graph_analyzer()
result = json.dumps(data)
```

## Related

- [[Dream-Link]] — consumes the isolated nodes this produces
- [[Dream-Validate]] — compares before/after against this snapshot
- [[Dream-Pass]] — the orchestrator that calls this first