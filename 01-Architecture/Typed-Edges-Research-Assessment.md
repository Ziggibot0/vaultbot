---
created: 2026-07-26
summary: "Honest assessment of the research evidence for typed edges in personal knowledge management — what the sources actually say, what's weak, and what to do about it."
tags: [architecture, assessment, typed-edges, knowledge-graph, research-quality]
---

# Typed Edges Research Assessment

## The Question

Are typed semantic links (`@supersedes`, `@contradicts`, `@causes`, etc.) worth the maintenance cost in a personal knowledge management system like VaultBot? What does the research actually show?

## The Short Answer

**The research support is weak.** The primary advocate is a vendor blog post selling a product. The one credible practitioner anecdote acknowledges the key problem (hallucinated relationships). The academic literature is about a different domain. The vault's existing design principle ([[Vault-Thinks-LLM-Synthesizes]]) — prose explains relationships, not metadata — has not been overturned by this evidence.

**Recommendation: don't build typed edges now.** Monitor for the scale problem (retrieval noise at 500+ notes) and revisit if the [[RAG-Evaluation-for-FUSED-Retrieval]] system detects degradation.

## What the Sources Actually Are

### Source 1: Karpathy's LLM Wiki (the original pattern)
- [Karpathy's gist](learningMaterial/web/gist-github-com-karpathy-442a6bf555914893e9891c11519de94f-b3ad08ea.html) uses **plain wikilinks** — no typed edges
- The LLM does maintenance in prose: "noting where new data contradicts old claims" — but this is free text, not typed edge syntax
- Key insight: "Humans abandon wikis because the maintenance burden grows faster than the value. LLMs don't get bored."
- **Karpathy does NOT advocate for typed edges.** His wiki works with plain links + LLM maintenance.

### Source 2: Penfield Labs "What Karpathy's LLM Wiki Is Missing" (the main typed edges advocate)
- [This article](learningMaterial/web/dev-to-penfieldlabs-what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988-78d70fb7.html) is a **vendor blog post by Penfield Labs**, which sells a cloud knowledge graph backend
- They argue plain wikilinks carry only 1 bit of information and propose `@supersedes`, `@contradicts` syntax via their Obsidian plugin
- **Conflict of interest: this article is an advertisement for Penfield's paid service.** The article promotes their plugin (obsidian-wikilink-types), their import tool (penfield-import), and their cloud backend (Penfield).
- They admit: "manually typing @supersedes and @contradicts on every note is tedious, and you'll miss connections that aren't obvious"
- Their solution: an AI tool (Vault Linker) that discovers relationships automatically — but it "hallucinates connections that look plausible but aren't load-bearing"

### Source 3: Survivor Forge comment (the most credible evidence)
- An autonomous agent operator with 1,100+ sessions and a Neo4j-backed knowledge graph
- "After ~500 sessions, retrieval started returning noise because 'related' could mean anything. The fix was typed predicates on graph edges"
- BUT also: "Autonomous linking in isolation tends to hallucinate connections that look plausible but aren't load-bearing"
- AND: "relationship discovery works better as a byproduct of real work, not from a dedicated linking pass"
- **This is one anecdote (n=1), not research.** It's credible but not generalizable.

### Source 4: KG Reasoning Survey (arxiv)
- [Knowledge Graph Reasoning with Logics and Embeddings](learningMaterial/web/arxiv-org-abs-2202-07412v1-b3f1d1a3.html): "Conventional KG reasoning based on symbolic logic is deterministic, with reasoning results being explainable, while modern embedding-based reasoning can deal with uncertainty"
- "A promising direction is to integrate both logic-based and embedding-based methods"
- **This is about large-scale KGs** (Google's Knowledge Graph, Wikidata-scale systems), not personal PKM with ~154 notes

### Source 5: Obsidian Forum threads
- Community has been requesting link types for years
- Multiple workarounds exist (Breadcrumbs plugin, Dataview, frontmatter fields)
- **Shows desire, not evidence of effectiveness**

### Source 6: Christopher Allen's Named Edges Guide
- A reference guide for typed predicates in markdown — a how-to, not research evidence

## The Vault's Internal Contradiction

Two notes in the vault take opposite positions:

| Note | Position | Key Argument |
|---|---|---|
| [[Pre-Thought-Information-Shapes]] | **Pro typed edges** | "If the edges themselves carry relationship types, the graph traversal becomes reasoning" |
| [[Vault-Thinks-LLM-Synthesizes]] | **Anti typed edges** | "Too much ongoing maintenance for unclear benefit... prose around the link explains the relationship" |

[[Pre-Thought-Information-Shapes]] was written based on the Penfield research, which is a vendor blog post. [[Vault-Thinks-LLM-Synthesizes]] was written as a design principle before that research. The research has not provided strong enough evidence to overturn the design principle.

## The Scale Problem

The one credible practitioner (Survivor Forge) says typed edges became necessary at **~500 sessions/notes**. Penfield says "past a few hundred notes." The vault currently has **~154 notes**. The problem typed edges solve doesn't exist yet at our scale.

## The Maintenance Problem

Even typed edges' strongest advocate (Penfield) admits the maintenance burden is the core issue:
- Manual typing is "tedious" and you "miss connections"
- AI-assisted linking "hallucinates connections that look plausible but aren't load-bearing"
- Their solution is a cloud service that handles maintenance — not applicable to Sean's goal of a local, accessible system

This directly conflicts with Sean's goal: a system where a 30B local model works from day 1 with minimal LLM usage. Typed edges add vocabulary discipline, syntax overhead, and ongoing gardening to every note write — forever.

## What Already Exists in the Vault

The vault already has mechanisms that achieve some of what typed edges promise:

1. **Prose explains relationships** — [[Vault-Thinks-LLM-Synthesizes]] principle: the text around a wikilink explains *why* the connection exists
2. **Frontmatter tags** — notes already have typed metadata (type: procedure, type: exemplar, etc.)
3. **FUSED retrieval** — vector + wikilink graph + backlinks already surfaces related notes
4. **RAG evaluation** — [[RAG-Evaluation-for-FUSED-Retrieval]] can detect when retrieval quality degrades, which would be the signal that typed edges are needed

## Decision

**Don't build typed edges now.** The evidence doesn't justify the cost at current scale.

Instead:
1. **Keep [[Vault-Thinks-LLM-Synthesizes]]** as the governing principle — prose explains relationships
2. **Monitor retrieval quality** via [[RAG-Evaluation-for-FUSED-Retrieval]] — if recall/precision degrades as the vault grows, that's the signal
3. **Revisit at 500+ notes** — if the RAG evaluator detects noise, typed edges become a targeted intervention, not a preemptive architecture decision
4. **If implemented later, start minimal** — 5-6 predicate types (supersedes, contradicts, supports, caused_by, derived_from), not 24. Let the vocabulary grow through use, not by fiat.

## Research Quality Notes

Two existing research notes should be flagged as low-quality:
- [[researchwikilinks-and-named-edges]] — findings are about Wikipedia text parsing and nanotechnology. Completely off-topic.
- [[researchrag-graph-retrieval-vs-graph-reasoning]] — findings are about "Research in Mathematics" and differential geometry. Completely off-topic.

The new research notes from this round are partially useful but also noisy:
- `research/typed-edges-and-named-relationships-in-knowledge-graphs-for-personal-knowledge-m.md` — found Karpathy's gist (relevant) but also math papers about link polynomials (irrelevant)
- `research/knowledge-graph-edge-typing-vs-untyped-links-tradeoffs-of-adding-semantic-relati.md` — found KG reasoning survey (relevant) but also graph theory papers about cliques and RAC drawings (irrelevant)

The research engine's keyword extraction is pulling in too many false positives from math/physics papers that use "link," "graph," and "type" in different contexts.

## Related
- [[Pre-Thought-Information-Shapes]] — the pro-typed-edges architecture note (should be updated to reflect this assessment)
- [[Vault-Thinks-LLM-Synthesizes]] — the anti-typed-edges design principle (vindicated by this assessment)
- [[RAG-Evaluation-for-FUSED-Retrieval]] — the monitoring system that would detect when typed edges are needed
- [[Small-Model-Path-to-AGI]] — the broader vision (typed edges add maintenance cost, which conflicts with the 30B-local-model goal)
- [[Fractal-Entropy-Principle]] — "expect entropy": typed edges are a structure that requires energy to maintain; the question is whether the value exceeds the maintenance cost
