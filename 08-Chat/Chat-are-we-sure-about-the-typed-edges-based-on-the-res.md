# Chat: are we sure about the typed edges based on the res

## 2026-07-26 03:18 UTC

**User:** are we sure about the typed edges based on the research?

**Assistant:** Good question — and honestly, the answer is **no, not fully**. Here's the real picture:

## What the Research Actually Shows

**One solid source:** [[typed-wikilinks-and-semantic-relationships-in-personal-knowledge-management-how-]] (15 sources, 20 facts) — found the Penfield/Karpathy work on named edges (`@supersedes`, `@contradicts`, etc.). This is legit and directly relevant.

**Two research notes went off-topic:**
- [[researchwikilinks-and-named-edges]] — findings are about Wikipedia text parsing and nanotechnology. Not about named edges at all.
- [[researchrag-graph-retrieval-vs-graph-reasoning]] — findings are about "Research in Mathematics" and differential geometry. Completely missed the topic.

**One partially relevant:** [[semantic-knowledge-graph-structure-that-encodes-reasoning-in-edges-between-nodes]] — has some useful findings about knowledge graphs and reasoning, but general.

## The Bigger Problem: The Vault Contradicts Itself

[[Vault-Thinks-LLM-Synthesizes]] **explicitly rejected typed edges**:

> "No typed edges, no abstraction cache, no argument builder. These were considered but rejected — too much ongoing maintenance for unclear benefit."
> "Wikilinks are citations, not typed edges. `[[Related Note]]` is enough. The prose around the link explains the relationship."

And [[Chat-if-thats-the-best-way-to-do-it-then-yes-if-there]] laid out 3 options — typed edges was Option A, but Option B (pre-computed argument notes) and Option C (embedding-based inference) were also on the table.

## Bottom Line

The typed edges idea has **one good research source** and **one competing design principle** that says it's over-engineering. Two of the four research notes are basically junk (wrong topic entirely). 

Before building anything, I'd want to:
1. **Re-research** typed edges properly to replace the two junk notes
2. **Resolve the contradiction** between [[Pre-Thought-Information-Shapes]] (pro typed edges) and [[Vault-Thinks-LLM-Synthesizes]] (anti typed edges)

Want me to do that re-research now?

<details>
<summary>Thinking process</summary>

Sean is asking whether the typed edges approach is actually well-supported by the research. Let me look at what the vault has on this topic.

From the vault context, I can see several relevant notes:

1. [[Pre-Thought-Information-Shapes]] — the main architecture note proposing typed edges
2. [[typed-wikilinks-and-semantic-relationships-in-personal-knowledge-management-how-]] — research note (15 sources, 20 facts)
3. [[researchwikilinks-and-named-edges]] — research note (6 sources, 11 facts)
4. [[semantic-knowledge-graph-structure-that-encodes-reasoning-in-edges-between-nodes]] — research note (9 sources, 19 facts)
5. [[researchrag-graph-retrieval-vs-graph-reasoning]] — research note (9 sources, 24 facts)
6. [[Vault-Thinks-LLM-Synthesizes]] — this note actually REJECTS typed edges!
7. [[Chat-if-thats-the-best-way-to-do-it-then-yes-if-there]] — chat where multiple options were discussed

Importantly, [[Vault-Thinks-LLM-Synthesizes]] explicitly says:
- "No typed edges, no abstraction cache, no argument builder. These were considered in [[Ephemeral-Argument-Architecture]] but rejected — too much ongoing maintenance for unclear benefit."
- "Wikilinks are citations, not typed edges. `[[Related Note]]` is enough. The prose around the link explains the relationship."

And [[Chat-if-thats-the-best-way-to-do-it-then-yes-if-there]] discusses three options:
- Option A: Typed Edges (researched approach)
- Option B: Pre-Computed Argument Notes
- Option C: Embedding-Based Relationship Inference

So there's actually a tension in the vault. The research supports typed edges, but [[Vault-Thinks-LLM-Synthesizes]] explicitly rejected them. Let me also look at the research quality — some of the research notes seem to have gone off-topic. For instance, [[researchrag-graph-retrieval-vs-graph-reasoning]] seems to have research findings about "Research in Mathematics" and "Research in differential geometry" — that doesn't seem right at all. And [[researchwikilinks-and-named-edges]] has findings about Wikipedia text parsing and nanotechnology — also seems off-topic.

Let me be honest with Sean about what the research actually shows and where it's weak. Let me read the key notes more carefully.

Actually, I have enough context from the vault subgraph to give a solid answer. Let me summarize:

1. The research note on typed wikilinks (15 sources, 20 facts) seems legitimate — it found the Penfield/Karpathy work on named edges, wikilink-types with @supersedes, @contradicts, etc.
2. The research note on "wikilinks-and-named-edges" (6 sources, 11 facts) seems to have gone off-topic — findings about Wikipedia text parsing, nanotechnology, etc.
3. The research note on "rag-graph-retrieval-vs-graph-reasoning" (9 sources, 24 facts) also seems off-topic — findings about "Research in Mathematics" and differential geometry.
4. The research note on "semantic-knowledge-graph-structure-that-encodes-reasoning-in-edges-between-nodes" (9 sources, 19 facts) has some relevant findings about knowledge graphs and reasoning.

So the research is mixed — one solid source (typed wikilinks), two that went off-topic, and one that's partially relevant.

Meanwhile, [[Vault-Thinks-LLM-Synthesizes]] explicitly rejected typed edges in favor of "the vault thinks, the LLM synthesizes" — plain wikilinks with prose explaining relationships.

I should be honest with Sean about this tension. The research has some support but also has quality issues, and there's a competing design principle in the vault that rejected the approach.

</details>