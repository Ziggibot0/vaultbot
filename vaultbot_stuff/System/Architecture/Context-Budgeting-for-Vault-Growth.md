---
created: 2026-07-26
updated: 2026-08-03
summary: How to manage the LLM context window as the vault grows past hundreds of notes — ranking, truncating, and compacting retrieved subgraphs to stay within budget.
tags:
  - architecture
  - context-window
  - retrieval
  - scaling
  - hermeneutics
type: architecture
status: verified
---

# Context Budgeting for Vault Growth

## The Problem

Right now, VaultBot retrieves a subgraph of ~20-30 notes per query. At the time this note was originally written (2026-07-26), the vault had 154 notes and this fit comfortably in the context window. As of 2026-08-03, the vault has grown to **1,285 notes** — an 8x increase in 8 days. The vault is now well past the scale this note originally described, and the budgeting strategies below are no longer hypothetical — they are urgently needed.

As the vault grows, two things happen:

1. **Retrieved subgraphs get larger** — more notes match each query, more connections exist
2. **Context quality degrades** — even with a large context window, models suffer from "lost in the middle" effects where information in the middle of long contexts gets ignored [sources: LLM Context Window Management and Long-Context Strategies 2026]

Without a context budgeting strategy, the system degrades silently as the vault grows. This is the scaling problem identified in [[Vault-Longevity-Architecture]].

## What the Research Says

### Three Strategies from Anthropic

Anthropic's context engineering work identifies three approaches for long-running agents [sources: Context engineering: memory, compaction, and tool clearing]:

1. **Memory** — Persist state externally (the vault already does this — notes ARE external memory)
2. **Compaction** — Summarize older context to free up space for new information
3. **Tool clearing** — Drop stale tool results from context when they're no longer needed

### Context Window Realities

- Advertised context limits rarely match effective performance — a 128K window doesn't mean 128K of *useful* attention
- Models lose information in the middle of long contexts (the "lost in the middle" problem)
- Smaller models are more susceptible to this — they have less capacity to extract signal from noise

### FILCO: Context Filtering

**FILCO** (Filter Context) filters irrelevant or low-utility spans from retrieved passages *before* generation [sources: Retrieval-Augmented Generation: A Comprehensive Survey of Architectures ...]. This improves both faithfulness and efficiency — the model only sees the relevant parts of each note.

## How This Applies to VaultBot

### The Context Budget

Define a token budget for the retrieved subgraph (e.g., 8K tokens for a 32K context model, leaving room for system prompt + chat history + response). Then:

1. FUSED retrieves candidate notes (may be 50+ notes)
2. Rank candidates by FUSED score (vector + graph + backlink)
3. Fill budget top-down until token limit reached
4. For notes that partially fit, truncate to most relevant sections
5. For notes that don't fit, drop them but log that they were available

### Node Ranking Strategy

| Factor | Weight | Rationale |
|---|---|---|
| FUSED relevance score | High | Primary signal — semantic + structural match |
| Note type | Medium | Directives > procedures > architecture > research > chat logs |
| Recency | Low | Recent notes may be more current, but old notes can be foundational |
| Note length | Negative | Longer notes consume more budget — prefer dense, well-structured notes |
| Wikilink density | Medium | Notes with more outgoing links are more connected (see [[Pre-Thought-Information-Shapes]]) |

### The Small Model Angle

This is especially critical for a 30B local model. Smaller models have:
- Smaller context windows (typically 4K-8K vs 128K+ for frontier models)
- Weaker long-context attention (more susceptible to "lost in the middle")
- Less ability to extract signal from noise

Context budgeting isn't optional for local inference — it's the difference between a working system and a broken one. See [[Cloud-Model-Obsolescence-Architecture]] for the full architecture of making the big model optional.

## Current Status (as of 2026-08-03)

- **Vault size**: 1,285 notes (up from 154 when this note was written — 8x growth in 8 days)
- **context_budgeter.py**: NOT YET BUILT. The module described below is still a design specification, not implemented code.
- **Retrieval**: The system currently retrieves subgraphs and includes them in context without explicit budgeting or truncation. The [[Filter-Context-For-Query]] procedure provides a single filter step but not a full multi-stage ranking pipeline.
- **Urgency**: HIGH. At 1,285 notes, retrieved subgraphs are already large enough that context quality degradation is a real risk, especially for smaller models.

## What Needs to Be Built

- A `context_budgeter.py` module that:
  - Takes FUSED retrieval results + a token budget
  - Ranks notes by the factors above
  - Truncates/fills to budget
  - Returns the curated subgraph + a manifest of what was dropped
- Integration with the chat handler to apply budgeting before building the system prompt
- A configurable token budget parameter (different for frontier vs 30B models)

## Related
- [[Vault-Longevity-Architecture]] — the scaling problem this solves
- [[Cloud-Model-Obsolescence-Architecture]] — the architecture for making the big model optional (small model context budgeting is critical to this)
- [[RAG-Evaluation-for-FUSED-Retrieval]] — measuring whether budgeting hurts retrieval quality
- [[Pre-Thought-Information-Shapes]] — typed edges help ranking
- [[Deterministic-Scaffolding-for-Small-Models]] — the framework does the heavy lifting
- [[Self-Assessment-Using-the-Knowledge-Triad]] — the gap this fills
- [[Filter-Context-For-Query]] — the existing single-step filter procedure (needs to be expanded into a full pipeline)