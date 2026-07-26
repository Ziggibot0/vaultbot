---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "a curated context budget causes the model to miss information that was available in the vault but was truncated, and the answer quality degrades as a result"
applies_to:
  - retrieval
  - context-management
  - small-models
depends_on:
  - "[[Context-Budgeting-for-Vault-Growth]]"
  - "[[RAG-Evaluation-for-FUSED-Retrieval]]"
sources:
  - "https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools"
  - "https://zylos.ai/research/2026-01-19-llm-context-management/"
  - "https://arxiv.org/html/2501.00309v2"
---

# How to Manage Context Budget

## When to Use This

Use this procedure when the retrieved subgraph exceeds the context budget (token limit). This applies to:
- When the vault has 200+ notes and queries return 30+ results
- When running on a 30B local model with a smaller context window
- When the system prompt + retrieved notes + chat history approach the model's context limit

## Steps

1. **Determine the token budget.** Calculate available tokens: `budget = model_context_limit - system_prompt_tokens - chat_history_tokens - response_reserve`. For a 32K model: ~4K system prompt + ~4K chat history + ~4K response reserve = ~20K for retrieved notes. For an 8K model: ~1K + ~1K + ~1K = ~5K for notes.

2. **Rank retrieved notes by priority.** Apply the ranking factors:
   - FUSED relevance score (highest weight)
   - Note type: directives > procedures > architecture > research > chat logs
   - Wikilink density (more connected = more relevant)
   - Note length (shorter = more efficient, prefer dense notes)

3. **Fill the budget top-down.** Add notes in ranked order until the token budget is reached. Stop when the next note would exceed the budget.

4. **Truncate partially-fitting notes.** If a high-priority note is too long to fit fully, include only its most relevant sections (summary + first section). Drop the rest.

5. **Log what was dropped.** Record which notes were available but not included. This is the "drop manifest" — if Sean reports missing information, check if it was in the drop manifest (retrieval failure) or not retrieved at all (search failure).

6. **Compact chat history if needed.** If chat history is consuming too much budget, summarize older exchanges into a single paragraph. Keep the most recent 3-5 exchanges verbatim.

7. **Verify the curated context.** After budgeting, check: does the curated subgraph still contain the notes needed to answer the query? If not, the budget is too tight — increase it or improve ranking.

## Falsifiability

This procedure is falsifiable: if budgeting causes the model to miss available information and answer quality drops, the ranking or budget is wrong. Log it as a procedure failure.

## Related
- [[Context-Budgeting-for-Vault-Growth]] — the architecture this procedure implements
- [[Small-Model-Path-to-AGI]] — why this is critical for 30B models
- [[RAG-Evaluation-for-FUSED-Retrieval]] — measuring whether budgeting hurts quality
- [[Vault-Longevity-Architecture]] — the scaling problem this solves
- [[Procedural-Bootstrap-and-Evolution-Plan]] — where this fits in the evolution roadmap
