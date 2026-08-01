---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Analyze the vault graph's connectedness and find islands."
when_to_use: "When checking vault structure for disconnected note clusters."
allowed_tools: [vault_graph_analyzer]
---

# Vault-Graph-Analyzer

Analyze the connectedness of the vault's .md files. Finds islands (connected components), measures hub-and-spoke structure, and identifies isolated notes with no links.

## Steps

1. ```python
   result = vault_graph_analyzer()
   print(result)
   ```

2. [llm: Review the analysis. Islands indicate notes that need linking to the rest of the vault.]