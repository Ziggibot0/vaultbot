---
type: procedure
status: active
baseline: true
created: 2026-07-31
description: Analyze the vault graph's cluster structure and communities.
when_to_use: When checking for topic clusters and community structure in the vault.
allowed_tools:
  - vault_cluster_analyzer
summary: Vault-Cluster-Analyzer
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Vault-Cluster-Analyzer

Analyze the vault graph's cluster structure: identifies communities using label propagation, counts clusters, and finds the largest connected components.

## Why This Exists

Topic clusters and community structure in the vault weren't visible without a dedicated analysis. This procedure exists to identify communities via label propagation and find the largest connected components. The key tradeoff: it delegates the heavy lifting to the vault_cluster_analyzer tool and uses the LLM only to review the results for MOC (map of content) opportunities.

## Steps

### Step 1: Analyze the vault graph's cluster structure

1. ```python
   from custom_tools.vault_cluster_analyzer import run as _cluster
   result = _cluster({"vault_path": args.get("vault_path", "")})
   print(result)
   ```

### Step 2: Review the cluster analysis

2. [llm: Review the cluster analysis. Large clusters may need MOC (map of content) notes to organize them.]

## Related

- [[Vault-Graph-Analyzer]] — analyzes vault graph connectedness and islands
- [[Vault-Health-Check]] — combines graph analysis into a health report
- [[Vault-Statistics]] — computes vault statistics