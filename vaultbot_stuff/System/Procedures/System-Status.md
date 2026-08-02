---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Report VaultBot's operational state and autonomous research history."
when_to_use: "When the user asks what you've been doing or what you can do."
allowed_tools: [run_procedure]
---

# System-Status

Report VaultBot's operational state. This delegates to [[VaultBot-Status]] (offline machine/model/vault snapshot). For the live autonomous-researcher cycle state, the model should call the `vaultbot_status` chat tool directly — it needs the running backend.

## Steps

1. ```python
   result = run_procedure("VaultBot-Status")
   print(result.get("final_output", ""))
   ```

2. [llm: Summarize the status for the user in a concise report.]