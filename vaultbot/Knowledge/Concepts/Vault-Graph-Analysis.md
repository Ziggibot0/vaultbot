---
type: concept
status: active
created: 2026-08-03
tags:
  - concept
  - vault-graph
  - connectedness
  - analysis
summary: "Analyzing the vault's wikilink graph structure — islands, density, diameter — to find disconnected notes and plan link improvements."
---

# Vault-Graph-Analysis

## What It Is

Vault-Graph-Analysis is the process of examining the connectedness of the vault's `.md` files through their wikilinks. The vault is a directed graph where each note is a node and each `[[wikilink]]` is an edge. Analyzing this graph reveals structural problems: orphan notes with no links, dense clusters that are internally connected but isolated from the rest, and overly long paths between related concepts.

## Why It Matters

A well-connected vault means retrieval works better — when the [[FUSED-Retrieval]] system follows links, it can reach more relevant context. Islands (connected components with no links to the main graph) are invisible to link-following retrieval and represent knowledge that is effectively cut off from the rest of the vault.

## How to Do It

Use the `vault_graph_analyzer` tool, which finds:
- **Islands**: connected components — groups of notes linked to each other but not to the main graph
- **Density**: ratio of actual edges to possible edges
- **Diameter**: longest shortest-path between any two notes
- **Average path length**: how many hops it takes on average to get from one note to another

## Related

- [[Vault-Cluster-Analysis]] — finer-grained community detection within the graph
- [[vault_graph_analyzer]] — the tool that performs this analysis
- [[Vault-Gaps]] — finding knowledge gaps (dangling links, thin notes)