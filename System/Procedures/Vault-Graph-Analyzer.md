---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Analyze the vault graph's connectedness and find islands.
when_to_use: When checking vault structure for disconnected note clusters.
allowed_tools:
  - vault_graph_analyzer
summary: Vault-Graph-Analyzer
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Vault-Graph-Analyzer

Analyze the connectedness of the vault's .md files. Finds islands (connected components), measures hub-and-spoke structure, and identifies isolated notes with no links.

## Why This Exists

Disconnected note clusters and isolated notes weren't visible without a graph analysis. This procedure exists to find islands, measure hub-and-spoke structure, and identify notes with no links. The key tradeoff: it delegates to the vault_graph_analyzer tool and uses the LLM only to review the results for islands that need linking.

## Steps

### Step 1: Analyze the vault graph's connectedness

1. ```python
   result = vault_graph_analyzer()
   print(result)
   ```

### Step 2: Review the analysis for islands

2. [llm: Review the analysis. Islands indicate notes that need linking to the rest of the vault.]

## Related

- [[Vault-Cluster-Analyzer]] — analyzes cluster structure and communities
- [[Vault-Health-Check]] — combines graph analysis into a health report
- [[Find-Orphans]] — finds orphan notes