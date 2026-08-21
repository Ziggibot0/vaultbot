---
type: semantic
status: tentative
baseline: true
created: 2026-07-27
last_reviewed: 2026-07-27
review_interval_days: 60
evidence_count: 3
evidence_sources:
  - "[[Chat-remember-this-shouldnt-be-bespoke-to-ollama-so-we]]"
  - "[[Chat-wait-a-minute-make-sure-that-anything-that-calls-a]]"
  - "[[Chat-NO-the-vaultbot-framework-should-work-from-day-1]]"
scope:
  - architecture
  - external-tools
  - portability
falsifiable_if: a future session occurs where the operator does not correct VaultBot for hardcoding local LLM connections (like direct Ollama calls) instead of using configured endpoints
tags:
  - semantic
  - pattern
  - architecture
  - constraints
  - sean-preferences
summary: LLM Endpoint Abstraction Constraint
---

# LLM Endpoint Abstraction Constraint

## How This Note Was Generated

This note was produced by deterministic pattern extraction across chat logs in `vaultbot/chat/`. The extractor scanned for the operator's directives regarding external tool usage and LLM integration. It specifically flagged corrections where VaultBot proposed hardcoding local model connections instead of using abstracted, configurable endpoints.

## Pattern 1: Prohibition of Bespoke LLM Connections

**The pattern:** When designing features that require external intelligence (RAG, summarization, reasoning), the operator strictly forbids hardcoding direct connections to local models (like Ollama) or bespoke implementations. All LLM calls must be routed through a standardized abstraction layer using configured endpoints and API keys.

**Evidence:**
- "remember this shouldn't be bespoke to ollama so we need to make sure that any time an llm is called its through the endpoints and api keys that the us" — the operator corrects VaultBot for tying the framework to a specific local model implementation
- "wait a minute make sure that anything that calls an LLM is calling it through the configured model NOT a direct connection to the ollama endpoint, oth" — the operator enforces the architectural rule that the vault must remain portable and not dependent on a single local inference engine
- "NO. the vaultbot framework should work from day 1 with a small 30b local model and shouldn't require a large model to trailblaze for it. through intel" -- the operator reinforces the constraint that the framework's core logic must not rely on bespoke dependencies or trailblazing via external heavy models

**Semantic rule:** Any module or feature requiring LLM capabilities must be designed with an abstracted interface. The actual LLM provider (local Ollama, remote API, etc.) should be injected via configuration/API keys at runtime. Never instantiate a direct connection to a specific model server within the core logic of VaultBot's procedures.

**Prevention:** When drafting architectural plans or writing code that interacts with language models, always default to using the vault's existing LLM manager utility or define a clear `llm_endpoint` configuration variable.

## Related

- [[Deterministic-Scaffolding-for-Small-Models]] -- explains why portability and avoiding bespoke dependencies are critical for small-model viability
- [[Execution-Loop-Dominance-Pattern]] -- ensures that these abstracted calls fit cleanly into the standard execution workflow without breaking the loop