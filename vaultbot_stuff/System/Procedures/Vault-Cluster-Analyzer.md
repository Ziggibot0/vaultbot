---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Analyze the vault graph's cluster structure and communities."
when_to_use: "When checking for topic clusters and community structure in the vault."
allowed_tools: [vault_cluster_analyzer]
---

# Vault-Cluster-Analyzer

Analyze the vault graph's cluster structure: identifies communities using label propagation, counts clusters, and finds the largest connected components.

## Steps

1. ```python
   result = vault_cluster_analyzer()
   print(result)
   ```

2. [llm: Review the cluster analysis. Large clusters may need MOC (map of content) notes to organize them.]