---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Run the graph analyzer again and compare before/after metrics. Verifies the graph is healthier after the dream pass. Takes the 'before' metrics from Dream-Analyze as input.
when_to_use: As part of a Dream-Pass cycle, or standalone after graph maintenance work.
falsifiable_if: islands_after > islands_before or connectivity_after < connectivity_before
applies_to:
  - vault
  - graph-health
  - validation
allowed_tools:
  - vault_graph_analyzer
  - run_procedure
summary: Dream-Validate
tags:
  - procedure
  - procedures
---

# Dream-Validate

Verifies that graph maintenance work actually improved the vault's connectivity. Compares before/after metrics.

## Why This Exists

Graph maintenance work (linking, gap-filling) is only worth doing if it actually improves connectivity, and without a before/after comparison there's no way to know. This procedure exists to re-run the graph analyzer and compare metrics against the pre-pass snapshot. The key tradeoff is a hard falsifiable gate — if islands increased or connectivity dropped, the pass is judged to have failed.

## Steps

### Step 1: Compare before/after graph metrics

1. ```python
import json

# Get "before" metrics — either from prior_results (if Dream-Analyze ran first)
# or from the procedure's input
try:
    _analyze_data = json.loads(prior_results[0]) if len(prior_results) > 0 else {}
except:
    _analyze_data = {}

# Handle nested structure from vault_graph_analyzer (returns {"status": ..., "analysis": {...}})
if "analysis" in _analyze_data:
    _analyze_data = _analyze_data["analysis"]

islands_before = _analyze_data.get("num_islands", 0)
connectivity_before = _analyze_data.get("connectivity_ratio", 0)
isolated_before = len(_analyze_data.get("isolated_nodes", []))

# Run graph analyzer for "after" metrics
data_after = vault_graph_analyzer()
# Handle nested structure
if "analysis" in data_after:
    data_after = data_after["analysis"]

islands_after = data_after.get("num_islands", 0)
connectivity_after = data_after.get("connectivity_ratio", 0)
isolated_after = len(data_after.get("isolated_nodes", []))

result = json.dumps({
    "islands_before": islands_before,
    "islands_after": islands_after,
    "connectivity_before": connectivity_before,
    "connectivity_after": connectivity_after,
    "isolated_before": isolated_before,
    "isolated_after": isolated_after,
    "orphans_resolved": isolated_before - isolated_after,
    "graph_improved": islands_after <= islands_before and connectivity_after >= connectivity_before,
})
```

## Related

- [[Dream-Analyze]] — produces the "before" metrics this compares against
- [[Dream-Pass]] — the orchestrator that calls this
- [[Dream-Link]] — the linking work this validates