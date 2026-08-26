---
type: procedure
status: verified
baseline: true
created: 2026-07-31
description: "Report VaultBot's operational state: backend status, autonomous researcher state, index/graph stats, and current model. Use when the user asks what you've been doing or what you can do."
when_to_use: when the user asks about status, health, or what VaultBot is doing
applies_to:
  - status
  - diagnostics
allowed_tools:
  - vault_graph_analyzer
  - vault_list
summary: "Summary: A Python script analyzes VaultBot status by extracting machine specs (OS/RAM/GPU), ollama loaded models list, and vault graph statistics to generate offline metadata for autonomous-researcher"
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# VaultBot-Status

## When to Run This

Run when the user asks about status, health, hardware, which models are loaded, or what VaultBot is running on. This reports everything that can be determined OFFLINE: machine specs (OS/CPU/RAM/GPU), Ollama models loaded, and vault graph/index stats. (For the live autonomous-researcher cycle count use the `vaultbot_status` chat tool — that needs the running backend; this procedure is the offline snapshot.)

## Why This Exists

Users ask about status, health, and hardware, but the live status tool needs a running backend. This procedure exists to report everything determinable offline — machine specs, loaded models, and vault stats. The key tradeoff: it's an offline snapshot, so it deliberately excludes live autonomous-researcher cycle state, which requires the running backend.

## Steps

### Step 1: Gather machine spec + graph stats

1. ```python
import json

from custom_tools.machine_spec import run as _spec
spec = _spec({})

graph = vault_graph_analyzer()
analysis = graph.get("analysis", {}) if isinstance(graph, dict) else {}

ollama = spec.get("ollama", {})
models = ollama.get("models", []) if isinstance(ollama, dict) else []

result = json.dumps({
    "machine": {
        "os": spec.get("os", {}),
        "ram_gb": spec.get("ram", {}).get("total_gb"),
        "gpu": spec.get("gpu", {}),
    },
    "ollama_running": ollama.get("running") if isinstance(ollama, dict) else None,
    "models_loaded": models,
    "vault": {
        "notes": analysis.get("num_nodes"),
        "islands": analysis.get("num_islands"),
        "connectivity": analysis.get("connectivity_ratio"),
    },
})
```

### Step 2: Report the status

2. [llm: Report VaultBot's status from the prior step output in a clear, concise summary: machine (OS, RAM, GPU), whether Ollama is running and which models are loaded (call out the big/small/vision cartridges if identifiable), and vault size (notes, connectivity). Be natural — don't dump JSON. If the user likely wanted live research-cycle state, note they can ask "what have you been doing" which uses the live status tool.]

## Related

- [[System-Status]] — the system status report procedure
- [[Vault-Health-Check]] — the vault health snapshot
- [[Vault-Graph-Analyzer]] — the graph analysis this procedure consumes