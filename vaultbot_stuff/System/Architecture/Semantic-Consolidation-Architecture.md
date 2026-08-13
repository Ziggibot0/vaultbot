---
type: architecture
status: draft
baseline: true
created: 2026-07-26
updated: 2026-08-03
record: true
superseded_by: "chat_handler.py (2026-08-02 refactor — consolidation was removed)"
summary: "HISTORICAL: Describes a consolidation architecture where the framework extracts patterns and the LLM only synthesizes pre-extracted results. This was never built, and the 2026-08-02 refactor explicitly removed all consolidation behavior from the chat loop. Preserved for design history."
tags: [architecture, memory, consolidation, semantic, episodic, deterministic, automation, record]
depends_on:
  - "[[Procedural-Bootstrap-and-Evolution-Plan]]"
  - "[[Deterministic-Scaffolding-for-Small-Models]]"
  - "[[Vault-Longevity-Architecture]]"
  - "[[Calibration-via-Operator-Feedback]]"
sources:
  - "https://arxiv.org/html/2603.07670v1"
  - "https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/"
  - "https://arxiv.org/abs/2605.20616v1"
  - "https://arxiv.org/abs/2601.02845v2"
  - "https://arxiv.org/abs/2303.11366v4"
---

# Semantic Consolidation Architecture

> **⚠ RECORD — NOT CURRENT DOCUMENTATION**
> This note describes an architecture that was designed but never built. The 2026-08-02 refactor of `chat_handler.py` explicitly removed all consolidation behavior — the docstring states: "No phases, no gates, no forced convergence, no consolidation, no step summaries." This note is preserved for design history only. Do not use it to understand how the system works today.

## The Problem

Every session, VaultBot has experiences: it builds tools, writes notes, gets corrected by the operator, hits walls, and solves problems. These experiences are stored as **episodic memory** — chat logs in `vaultbot/chat/`, research notes, tool creation records. But they stay episodic. Next session, the LLM has to re-derive the same insights from scratch because nobody consolidated them into **semantic knowledge** — abstracted, de-contextualized patterns that are true across sessions.

This is the exact gap identified in the memory survey literature: "The consolidation step — where episodes become semantic knowledge — is particularly underserved: it typically requires either explicit developer rules or periodic LLM-driven summarization, both of which are fragile and hard to validate."

## The Design (Never Built)

The proposed architecture had three layers:

1. **Pattern extraction** — deterministic code scans chat logs and tool outputs for recurring patterns (failed approaches, successful fixes, operator corrections)
2. **Scaffolded abstraction** — the framework extracts the raw material, then the LLM synthesizes it into reusable knowledge notes
3. **Semantic storage** — consolidated patterns are written as linked notes in the vault, becoming permanent knowledge

This design was abandoned because the 2026-08-02 refactor took a different approach: the model is responsible for its own planning, tracking, and stopping. The framework stays simple — it just keeps the conversation bounded, streams output, and dispatches tools. Consolidation was removed entirely as unnecessary framework complexity.

## Why This Note Is Preserved

The research and design reasoning here remain valid reference material for anyone considering adding consolidation in the future. The decision to remove it was pragmatic (simplify the framework) rather than a rejection of the concept. If consolidation is ever re-introduced, this note provides the design foundation.

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] — the broader plan this was part of
- [[Deterministic-Scaffolding-for-Small-Models]] — the scaffolding philosophy
- [[Vault-Longevity-Architecture]] — long-term memory architecture
- [[Record-Convention]] — why this note is marked as a record