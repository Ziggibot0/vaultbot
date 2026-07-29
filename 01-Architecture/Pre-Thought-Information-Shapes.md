---
created: 2025-07-25
summary: "How to store information in the vault so that connections between notes encode reasoning — the graph does the thinking before the LLM is called."
tags: [architecture, knowledge-graph, pre-thought, framework]
---

# Pre-Thought: Information Shapes That Think

## The Problem

Right now, vault links are flat. `[[Example-Note]]` carries exactly one bit of information: "these two notes are connected." The *why* lives in the prose around the link, invisible to every tool in the system. When the LLM receives a subgraph of notes, it has to synthesize the relationships from scratch — reading prose, inferring connections, guessing at arguments. That's the hard part, and it's exactly what a weak model can't do well.

## The Insight

If the edges themselves carry relationship types, the graph traversal becomes reasoning. Walking from Note A → Note B along a `contradicts::` edge is a different inference step than walking along a `derived_from::` edge. The traversal path forms an argument chain. By the time the LLM reads the subgraph, the typed edges have already assembled the thought — the LLM just narrates it.

This is "pre-thought": the information is stored in shapes where the connections form coherent thoughts. The vault does the reasoning through its structure. The LLM relays.

## The Three Mechanisms

### 1. Typed Edges (Named Predicates)

From [[typed-wikilinks-and-semantic-relationships-in-personal-knowledge-management-how-|the Penfield/Karpathy research]] and [[Wikilink|Christopher Allen's named edges guide]]:

Instead of flat `[[Note]]`, use typed predicates:

```
- contradicts::[[Old Hypothesis]]
- derived_from::[[Source Paper]]
- supports::[[Core Argument]]
- supersedes::[[Previous Analysis]]
- extends::[[Base Concept]]
- caused_by::[[Root Decision]]
- prerequisite_for::[[Advanced Topic]]
```

The edge type IS the reasoning step. A traversal that follows `caused_by::` → `contradicted_by::` → `superseded_by::` is an argument, not a topic cluster.

**Key vocabulary** (curated, not freeform — see the folksonomy-vs-ontology section below):

| Category | Predicates | What it encodes |
|----------|-----------|-----------------|
| Provenance | `derived_from::`, `extracted_from::`, `informed_by::` | Where knowledge came from |
| Structural | `extends::`, `implements::`, `composes_with::` | How concepts build on each other |
| Tension | `contradicts::`, `invalidated_by::`, `supersedes::` | Where knowledge conflicts |
| Support | `supports::`, `validated_by::`, `confirmed_by::` | What backs a claim |
| Causal | `caused_by::`, `causes::`, `enables::` | Why things happen |
| Generative | `proposes::`, `resolved_by::`, `generates::` | What produces what |
| Lifecycle | `evolved_into::`, `replaced_by::`, `deprecated_by::` | How knowledge changes over time |

### 2. Annotated Edges

From Christopher Allen's guide: an indented annotation beneath each predicate line explains *why* the relationship matters:

```
## Relations
- contradicts::[[Old Caching Approach]]
  - The new benchmark data shows 3x worse latency under load; the old approach assumed steady-state only.
- extends::[[Base Retrieval Architecture]]
  - Adds the typed-edge layer that the base architecture didn't need at small scale.
```

This is **progressive disclosure**: the predicate gives direction, the annotation gives rationale, the target file gives depth. An agent (or the retrieval system) can decide which edges to traverse *without reading the target files*. The annotation is the pre-thought — it tells you what to expect if you follow the link.

### 3. Traversal as Reasoning

From [[researchrag-graph-retrieval-vs-graph-reasoning|the WhyHow.AI graph reasoning research]]:

> "Knowledge graphs are not just data stores; they can also be reasoning structures."

Graph Retrieval = using the graph to find related context (search-augmenting).
Graph Reasoning = using the graph to deterministically navigate information (the traversal path IS the argument).

The key insight: **how information is retrieved matters more than what's in the graph.** A retrieval path that follows `caused_by::` → `supports::` → `contradicted_by::` is a causal argument with a counterpoint. The LLM doesn't have to figure out that chain — the graph already encodes it.

## What This Means for VaultBot

### Current State (Flat Graph)
- `vault_graph.py` extracts wikilinks with `WIKILINK_RE` — captures only the target, no relationship type
- Edges stored as `Dict[str, Set[str]]` — source → set of targets, no type info
- `fused_retrieval.py` uses graph/backlink channels but all edges are identical gray lines
- `abstract_context.py` builds L2/L1/L0 views but doesn't use edge types
- The LLM receives a subgraph of notes with no relationship information — it has to infer everything

### Target State (Typed Graph)
- `vault_graph.py` parses both flat `[[wikilinks]]` AND typed `predicate::[[wikilinks]]`
- Edges stored as `Dict[str, Dict[str, List[str]]]` — source → {predicate_type: [targets]}
- `fused_retrieval.py` weights edges by type (a `contradicts::` edge is more informative than a `relates_to::` edge)
- `abstract_context.py` presents typed edge chains to the LLM: "Note A `contradicts::` Note B because [annotation]"
- The LLM receives a subgraph where relationships are explicit — it just narrates

### The Chain

1. **Write notes with typed edges** — every link gets a predicate. The annotation explains why.
2. **Parse typed edges** — the graph builder extracts both the target and the relationship type.
3. **Retrieve along typed edges** — the fused retriever follows edges by type, not just proximity. A `caused_by::` chain is a causal argument; a `contradicts::` chain is a debate.
4. **Present typed chains to the LLM** — the context builder assembles the subgraph as a typed edge chain, not a bag of related notes. The LLM sees the argument pre-assembled.

## Vocabulary Discipline

From the research: start with a small core vocabulary (10-20 predicates). Let it grow through use. Prune periodically. The gardening metaphor:

- **Weeding** — remove redundant/ambiguous predicates
- **Seeding** — introduce specific predicates where only broad ones exist
- **Fertilizing** — enrich predicates with clearer definitions

Rules:
- Multi-word predicates with underscores (`derived_from`, not `source`)
- `conforms_to::` over `is_a::` (compliance, not identity)
- `relates_to::` is a last resort, not a first instinct
- No synonyms — if `derived_from::` exists, don't create `sourced_from::`

## The Living Graph

From the Penfield discussion: "nodes that get traversed stay accurate, the ones nobody queries drift." The graph gets more accurate through use. Relationship discovery works better as a byproduct of real work than as a dedicated linking pass.

This means: when I research a topic and write a note, I should type the edges as I write. When I answer a question and notice a connection, I should add a typed edge. The graph grows organically through use, not through a one-time linking pass.

## Sources

- [[typed-wikilinks-and-semantic-relationships-in-personal-knowledge-management-how-|Typed wikilinks and semantic relationships in PKM]] — Penfield/Karpathy article on typed `@` relationships in Obsidian
- [[Wikilink|Wikilinks and Named Edges]] — Christopher Allen's agent reference guide for typed predicates in markdown
- [[researchrag-graph-retrieval-vs-graph-reasoning|Graph Retrieval vs Graph Reasoning]] — WhyHow.AI on knowledge graphs as reasoning structures
- [[semantic-knowledge-graph-structure-that-encodes-reasoning-in-edges-between-nodes|Semantic knowledge graph structure]] — general research on encoding reasoning in edges

---

## Philosophical Grounding

This note's proposal for typed edges is grounded in **ontology** — the philosophical study of what kinds of entities exist and how they relate. The typed-edge vocabulary (provenance, structural, tension, support, causal, generative, lifecycle) IS an ontology definition for the vault. See [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] for how this connects to epistemology (validation) and hermeneutics (interpretation) as a complete philosophical framework for the vault.


---

## Update: 2026-07-26 — Research Assessment

This note's proposal for typed edges was based primarily on the [Penfield Labs blog post](learningMaterial/web/dev-to-penfieldlabs-what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988-78d70fb7.html), which is a **vendor advertisement for a paid cloud service**. A full assessment of the research evidence is in [[Typed-Edges-Research-Assessment]].

**The verdict: the research does not justify building typed edges at current scale (~154 notes).** The key findings:

1. **Karpathy himself doesn't use typed edges** — his LLM Wiki pattern works with plain wikilinks + LLM maintenance in prose
2. **The one credible practitioner** (Survivor Forge, 1,100+ sessions) says typed edges became necessary at ~500 notes, not 154 — and warns that autonomous linking "hallucinates connections that look plausible but aren't load-bearing"
3. **The maintenance cost is real** — even Penfield admits manual typing is "tedious" and AI-assisted linking produces false positives
4. **[[Vault-Thinks-LLM-Synthesizes]] is vindicated** — prose explaining relationships remains the simpler, lower-maintenance approach

**This note is now superseded by [[Typed-Edges-Research-Assessment]] as the authoritative source on this topic.** The typed-edge architecture described above remains a valid *future option* if retrieval quality degrades at scale, but it should not be implemented preemptively. The [[RAG-Evaluation-for-FUSED-Retrieval]] system will detect when it's needed.
