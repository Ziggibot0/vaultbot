---
type: concept
status: active
baseline: true
created: 2026-08-03
tags:
  - concept
  - conversation
  - context-management
  - procedures
  - architecture
summary: SUMMARY
---

# Sliding-Window-Conversation-Trail-Tools-as-Procedures-Spec

## Claim

Conversation context should be managed as a sliding window where older turns are summarized and compressed, while tools are encoded as procedures that persist in the vault — this combination ensures that no cognitive work is lost across context boundaries while keeping the active context small enough for efficient inference.

## Reasoning

The problem with unbounded conversation context is that it grows until it exceeds the model's context window, at which point either old context is silently dropped (losing information) or the system degrades in quality (too much noise). The sliding window pattern addresses this by keeping only the most recent N turns in full fidelity and compressing older turns into summaries. Therefore, the sliding window is necessary because unbounded context degrades model performance, and compression preserves signal while reducing token cost.

The key insight is that tools should not live in conversation context at all — they should live in the vault as procedures (see [[How-to-Create-a-Procedure]]). This means:
- Tool definitions don't consume context tokens
- Tool behavior is deterministic and reproducible
- Tool improvements persist across conversations
- The model's job is to *select and orchestrate* procedures, not to *contain* them

This maps to the [[Deterministic-Scaffolding-for-Small-Models]] principle: the intelligence is in the scaffolding (procedures + vault notes), not in the model's weights or context window. Because the scaffolding persists in the vault, it survives context resets and model swaps, making the system more reliable than approaches that rely on in-context tool definitions.

## Connection to VaultBot's Architecture

VaultBot already implements this pattern:
- **Sliding window**: The agentic loop framework manages working memory, consolidating completed steps into compact summaries
- **Tools as procedures**: The `execute_procedure` tool runs procedure notes deterministically
- **Chat consolidation**: The [[Chat-Consolidation]] procedure compresses conversation history into permanent notes

The spec formalizes what the system already does partially, making it explicit and auditable. Because formalization enables verification, this note serves as a reference point for evaluating whether the implementation matches the design.

## Research Backing

- [[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]] — iterative reasoning loops benefit from compressed context that preserves signal
- [[Deterministic-Scaffolding-for-Small-Models]] — scaffolding reduces dependence on model capacity
- [[Systems-where-infrastructure-makes-the-model-irrelevant-or-swappable-model-agnos]] — model-agnostic architecture through infrastructure

## Related

- [[Chat-Consolidation]] — implements the compression layer
- [[Execution-Loop-Dominance-Pattern]] — the loop that benefits from this architecture