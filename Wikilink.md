---
created: 2026-07-26
summary: "Wikilinks are the fundamental connecting tissue of the vault — flat `[[Note-Title]]` references that create a knowledge graph through plain-text markdown. They carry one bit of metadata (connection exists) while the reasoning lives in surrounding prose."
tags: [concept, wikilinks, knowledge-graph, obsidian, architecture]
---

# Wikilink

## What It Is

A wikilink is a plain-text reference between notes in an Obsidian vault, written as `[[Note-Title]]`. It creates a bidirectional edge in the knowledge graph — the target note knows it's being linked to (backlinks), and the source note explicitly references the target. No database, no API, no special syntax beyond double brackets around a note title.

## Why It Matters for VaultBot

Wikilinks are the edges of the knowledge graph that IS VaultBot's mind. The [[Vault-Longevity-Architecture]] depends on notes being interconnected — not isolated. FUSED retrieval (vector + wikilink graph + backlinks) uses these edges to find notes that are structurally related, not just semantically similar. When Sean asks a question, the retrieved subgraph follows wikilink paths to connected concepts.

## The One-Bit Problem

A plain wikilink carries exactly one bit of information: "these two notes are connected." The *why* — whether one note supports, contradicts, derives from, or extends another — lives in the prose around the link, invisible to every tool in the system. This is the core problem identified in [[Pre-Thought-Information-Shapes]]: the LLM has to synthesize relationships from scratch by reading prose, which is exactly what a small model can't do well.

## Typed Wikilinks: The Unresolved Question

The vault has extensive research on whether to add type information to wikilinks (e.g., `@supersedes::[[Note]]`, `@contradicts::[[Note]]`):

- **Research notes**: [[typed-wikilinks-and-semantic-relationships-in-personal-knowledge-management-how-|Typed wikilinks research]] and [[researchwikilinks-and-named-edges|Wikilinks and named edges research]] explore the concept
- **Architecture note**: [[Pre-Thought-Information-Shapes]] proposes typed edges as "pre-thought" — the graph does reasoning before the LLM is called
- **Assessment**: [[Typed-Edges-Research-Assessment]] concludes the evidence is weak — the main advocate is a vendor blog, and Karpathy's LLM wiki works fine with plain links + LLM maintenance

**Current decision**: Don't build typed edges now. The existing prose-around-links approach works, and the [[RAG-Evaluation-for-FUSED-Retrieval]] system will detect if retrieval quality degrades at scale, triggering a revisit.

## Wikilinks in the Knowledge Triad

In the [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics|Knowledge Triad]], wikilinks map to the **ontology** layer — they define what entities exist and how they relate structurally. The question of whether to add type information to edges is an ontological question: what kinds of relationships exist in the vault, and should they be explicit or implicit?

## Related

- [[Pre-Thought-Information-Shapes]] — how wikilinks could encode reasoning, not just connections
- [[Typed-Edges-Research-Assessment]] — honest assessment of typed edges evidence
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — wikilinks as the ontology layer
- [[Vault-Longevity-Architecture]] — why interconnected notes are the mind
- [[Vault-Thinks-LLM-Synthesizes]] — the design principle that prose explains relationships
