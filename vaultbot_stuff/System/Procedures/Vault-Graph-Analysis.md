---
type: procedure
status: verified
created: 2026-07-31
description: "Analyze the connectedness of the vault's .md files. Finds islands, measures hop distances, identifies isolated nodes, and suggests bridge edges."
when: "When analyzing vault structure, finding disconnected notes, or planning link improvements"
allowed_tools: [vault_search, vault_list]
---

# Vault-Graph-Analysis

Analyze the connectedness of the vault's .md files. Finds islands (connected components), measures hop distances, identifies isolated nodes, and suggests bridge edges to connect disconnected islands.

## Steps

1. ```python
   # Call the vault_graph_analyzer tool's run() function
   from custom_tools.vault_graph_analyzer import run as _analyze
   result = _analyze({"vault_path": args.get("vault_path", "")})
   print(result)
   ```