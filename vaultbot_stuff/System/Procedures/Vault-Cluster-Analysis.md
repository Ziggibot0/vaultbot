---
type: procedure
status: verified
created: 2026-07-31
description: "Analyze the vault graph's cluster structure: identifies communities, counts cross-cluster edges, finds sparse connection zones, and identifies which nodes should be connected but aren't."
when: "When analyzing vault cluster structure, finding sparse zones, or planning bridge connections"
allowed_tools: [vault_search, vault_list]
---

# Vault-Cluster-Analysis

Analyze the vault graph's cluster structure using label propagation. Identifies communities, counts cross-cluster edges, finds sparse connection zones, and identifies which nodes should be connected but aren't.

## Steps

1. ```python
   # Call the vault_cluster_analyzer tool's run() function
   from custom_tools.vault_cluster_analyzer import run as _cluster
   result = _cluster({"vault_path": args.get("vault_path", "")})
   print(result)
   ```