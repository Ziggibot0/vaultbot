---
type: concept
status: active
created: 2026-08-03
tags:
  - concept
  - vault-graph
  - clustering
  - community-detection
summary: "Analyzing the vault's cluster/community structure using label propagation to identify knowledge neighborhoods and bridge opportunities."
---

# Vault-Cluster-Analysis

## What It Is

Vault-Cluster-Analysis goes beyond [[Vault-Graph-Analysis]] by identifying communities within the vault's wikilink graph. While graph analysis tells you about overall connectedness (islands, density, diameter), cluster analysis tells you about the *structure* of the graph — which notes form natural neighborhoods and where those neighborhoods are weakly connected to each other.

## Why It Matters

Clusters reveal the vault's knowledge topology. A cluster of biology notes, a cluster of procedures, a cluster of chat logs — these are communities. When two clusters are only weakly connected (one or two bridge links), that's an opportunity: adding more cross-links between them improves retrieval and makes the vault more navigable.

## How to Do It

Use the `vault_cluster_analyzer` tool, which:
- Runs **label propagation** on the wikilink graph to detect communities
- Reports the number of clusters, their sizes, and their member notes
- Identifies **bridge nodes** — notes that link across cluster boundaries
- Suggests where additional cross-cluster links would strengthen the graph

## Related

- [[Vault-Graph-Analysis]] — broader connectedness analysis
- [[vault_cluster_analyzer]] — the tool that performs this analysis
- [[FUSED-Retrieval]] — how link structure affects retrieval quality