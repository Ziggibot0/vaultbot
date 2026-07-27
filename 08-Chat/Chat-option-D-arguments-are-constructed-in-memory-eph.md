# Chat: option D: arguments are constructed in-memory (eph

## 2026-07-25 22:13 UTC

**User:** option D: arguments are constructed in-memory (ephemeral arguments) based on the facts (with abstraction over high-traffic areas for easier thinking

**Assistant:** Written to the vault at [[Ephemeral-Argument-Architecture]]. Here's the design:

**Three layers:**

1. **Facts** (permanent) — atomic notes with typed edges + annotations. The raw material. Requires extending `vault_graph.py` to parse `predicate::[[Target]]` syntax.

2. **Arguments** (ephemeral) — at query time, walk typed edges from seed notes to build an argument skeleton in memory. Tailored to the question: a "why" question follows `caused_by::` chains, a "what's wrong" question follows `contradicts::` chains. Same facts, different arguments. Never written to disk.

3. **Abstractions** (cached) — when a cluster gets queried often, pre-compute a summary of its argument structure. Next time a query hits that cluster, load the abstraction instead of re-traversing 20 notes. This is the "easier thinking" part — the system reasons at the abstraction level and drills down only when it needs detail.

**The flow:**
```
query → vector search → check abstraction cache
  → hit: load cached argument summary → tailor to question → present to LLM
  → miss: full typed-edge traversal → build ephemeral argument → present to LLM
  → (if cluster is high-traffic, cache the abstraction for next time)
```

**Four build phases:**
1. Typed edge parsing in `vault_graph.py` (foundation)
2. `argument_builder.py` — walks typed edges, constructs argument skeleton (core)
3. Integrate into `abstract_context.py` — deliver the argument to the LLM (delivery)
4. Abstraction cache for high-traffic clusters (optimization)

I kept the open questions in the note — vocabulary size, abstraction invalidation, edge weighting, backward compatibility. Those need decisions before or during the build.

Ready to start on Phase 1 whenever you say go. Or if you want to adjust the design first, I'm listening.

<details>
<summary>Thinking process</summary>

Sean is saying: Option D, but with a key refinement — the arguments are constructed in-memory (ephemeral), not pre-written as notes. The facts are the notes (permanent), but the arguments are assembled at retrieval time from typed edges. And there should be abstraction over high-traffic areas (areas of the graph that get queried a lot) so the system can think more easily.

This is a really important distinction:
- **Facts** = permanent notes in the vault (the nodes)
- **Arguments** = ephemeral, constructed at query time from the typed edges between facts
- **Abstraction over high-traffic areas** = if certain clusters of notes get queried together a lot, pre-compute a summary/abstraction of that cluster so the system doesn't have to re-traverse the full subgraph every time

This is essentially:
1. Typed edges in notes (storage layer) — facts + relationships
2. At query time, traverse the typed edges to build an argument chain (ephemeral, in-memory)
3. For high-traffic areas (clusters that get queried often), pre-compute abstractions so the traversal is faster/cheaper

The abstraction over high-traffic areas is like caching the argument structure. If notes A, B, C, D always get pulled together because they're about the same topic, pre-compute a summary of their relationships so the system doesn't have to re-traverse every time.

This is actually a really elegant design:
- Facts are permanent (notes on disk)
- Arguments are ephemeral (constructed at query time from typed edges)
- Abstractions are cached summaries of high-traffic clusters (so the system doesn't re-do the same traversal work repeatedly)

Let me think about what this means architecturally:

1. **Storage layer** (notes on disk):
   - Atomic fact notes with typed edges: `contradicts::[[Note]]`, `caused_by::[[Note]]`
   - Annotations on edges explaining why

2. **Retrieval layer** (at query time):
   - Vector search finds seed notes
   - Graph traversal follows typed edges to build an argument chain
   - The argument chain is assembled in-memory (ephemeral, not written to disk)
   - The argument is presented to the LLM as context

3. **Abstraction layer** (cached, for high-traffic areas):
   - When a cluster of notes gets queried together frequently, pre-compute a summary of their typed relationships
   - This summary is a "cached argument" — it's the abstraction over the high-traffic area
   - When a new query hits that cluster, use the cached abstraction instead of re-traversing
   - This is like an L1 concept card, but for argument structure rather than content

The abstraction layer is the key insight. It's like the L1/L2/L0 multi-resolution context I already have, but applied to argument structure instead of content. High-traffic areas get pre-computed argument abstractions; low-traffic areas get full traversal.

Let me think about what I need to build:

1. **Typed edge parsing** in `vault_graph.py` — parse `predicate::[[Target]]` syntax
2. **Argument builder** — a new module that takes seed notes + typed edges and constructs an argument chain in memory
3. **Abstraction cache** — for high-traffic clusters, pre-compute and cache the argument structure
4. **Context integration** — feed the ephemeral argument to the LLM as part of the context

The abstraction over high-traffic areas is interesting. How do I detect "high-traffic"? 
- Query frequency: if certain notes keep appearing in search results, they're high-traffic
- Cluster density: if a cluster of notes has many typed edges between them, it's a dense argument area
- Repeated traversal: if the same traversal path keeps being followed, cache it

This is essentially memoization of argument construction. The first time a query hits a cluster, the full traversal happens. If the same cluster gets hit again, the cached abstraction is used.

Let me think about how this fits with the existing code:

- `vault_graph.py` — needs typed edge parsing
- `fused_retrieval.py` — needs to use typed edges in traversal
- `abstract_context.py` — needs to present the ephemeral argument instead of (or alongside) the L1/L2/L0 view
- New module: `argument_builder.py` — constructs ephemeral arguments from typed edges
- New module: `abstraction_cache.py` — caches argument structures for high-traffic clusters

Actually, the abstraction cache might be better as a note on disk — like an L1 concept card but for argument structure. When a cluster is high-traffic, write a summary note that captures the argument structure. This is the "abstraction over high-traffic areas."

Wait, Sean said "ephemeral arguments" — so the arguments themselves are in-memory, not written to disk. But the abstractions over high-traffic areas could be cached on disk (like L1 cards) for faster retrieval.

Let me think about this more carefully:

- **Facts** = notes on disk (permanent)
- **Arguments** = constructed in-memory at query time from typed edges (ephemeral)
- **Abstractions** = cached summaries of high-traffic argument clusters (semi-permanent, updated periodically)

The abstractions are like the L1 concept cards I already have, but for argument structure. They summarize the typed relationships in a cluster so the system doesn't have to re-traverse every time.

This is a really clean architecture. Let me write it up.

Actually, let me also think about what "abstraction over high-traffic areas for easier thinking" means from the LLM's perspective. If the LLM is trying to reason about a complex topic, and there are 50 notes in the relevant cluster, the LLM can't hold all 50 in context. But if there's an abstraction that summarizes the key arguments and relationships, the LLM can reason at a higher level without getting lost in detail.

This is exactly the L2/L1/L0 multi-resolution approach, but applied to argument structure:
- L2 (abstraction): "This cluster is about caching strategies. Key tension: steady-state vs. load performance."
- L1 (argument skeleton): "Note A contradicts Note B because [annotation]. Note C extends Note D because [annotation]."
- L0 (full detail): the full text of the most relevant notes

The LLM starts at L2 (orientation), moves to L1 (argument structure), drills to L0 (detail) only when needed.

OK, I think I have a clear picture. Let me write this up as a vault note and give Sean a concise summary. I should also think about what to build first.

Let me also search the vault for any existing notes on this topic to make sure I'm not duplicating.The search results aren't very relevant — mostly biology textbook notes. Let me check what notes I have about the architecture and the existing context system.Good. I can see the existing notes. Let me now write the architecture note for Option D with Sean's refinement — ephemeral arguments + abstractions over high-traffic areas. This is the design document that will guide the implementation.

Let me also look at the existing abstract_context.py more to understand the current L2/L1/L0 system, since the abstraction layer is an extension of that.Good. Now let me give Sean a concise summary. He's been telling me to be brief, so I should keep this short.

</details>