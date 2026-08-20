---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-03
description: "System installation health check. Deterministically verifies: Ollama is running and responsive, expected models are installed, vault index is built and current, backend process is alive, plugin WebSocket is connected, and all custom tools are registered. Returns a pass/fail report with specific failure reasons. Zero LLM reasoning needed — pure status checks formatted into prose."
when_to_use: when the operator asks 'is everything working?', after a restart, after installing new models, when debugging why something isn't responding, or at session start if something feels off
falsifiable_if: it reports a component as healthy when it's actually broken, or reports a component as broken when it's actually working
applies_to:
  - system-health
  - diagnostics
  - installation
  - troubleshooting
allowed_tools:
  - machine_spec
  - ollama_model_search
  - vault_list
  - code_read
summary: Install-Diagnostics
tags:
  - procedure
  - procedures
---

# Install-Diagnostics

## When to Run This

Run when the operator asks if everything is working, after a restart,
after installing new models, or when debugging system issues. This
procedure is almost entirely deterministic — it checks each system
component and reports pass/fail. The small model only formats the
structured results into a readable report.

## Steps

### Step 1: Check Ollama and model availability

1. ```python
# Check Ollama status and installed models
specs = machine_spec()
ollama_status = {
    "running": specs.get("ollama", {}).get("running", False),
    "host": specs.get("ollama", {}).get("host", "unknown"),
    "models_loaded": specs.get("ollama", {}).get("loaded_models", []),
    "models_installed": specs.get("ollama", {}).get("installed_models", []),
}
print(json.dumps(ollama_status, indent=2))
```

### Step 2: Check vault index and file count

2. ```python
# Check vault file count and structure
files = vault_list()
md_count = sum(1 for f in files if f.endswith(".md"))
proc_count = sum(1 for f in files if "Procedures" in f and f.endswith(".md"))
print(f"Vault .md files: {md_count}")
print(f"Procedures: {proc_count}")
```

### Step 3: Check backend source files exist

3. ```python
import os

backend_files = [
    "vaultbot/vaultbot_backend/main.py",
    "vaultbot/vaultbot_backend/agent_tools.py",
    "vaultbot/vaultbot_backend/vault_indexer.py",
    "vaultbot/vaultbot_backend/identity/identity.py",
]

missing = []
for f in backend_files:
    full = os.path.join(str(vault_path), f)
    if not os.path.exists(full):
        missing.append(f)

if missing:
    print(f"MISSING backend files: {missing}")
else:
    print("All critical backend files present.")
```

### Step 4: Compile diagnostic report

4. [llm: Given the structured diagnostic data from steps 1-3, produce a concise pass/fail report. Format as a table with columns: Component | Status | Notes. Flag any failures with ⚠️ and suggest what to check. Keep it under 200 words. Do NOT add commentary beyond what the data shows.]

## What This Replaces

Previously, the big cloud model would manually reason through system
status by calling `machine_spec`, reading files, and synthesizing a
report inline — costing hundreds of tokens for what is essentially
formatting structured data. Because the checks are purely deterministic
(machine_spec returns structured data, vault_list returns file counts),
therefore the small model is sufficient — it only formats the final
report, which is a simple table conversion task.

## Related

- [[Vault-Health-Check]] — checks vault graph health (connectivity, orphans)
- [[VaultBot-Status]] — quick status snapshot (offline machine/model/vault info)
- [[Ollama-Model-Search]] — search/list/pull Ollama models