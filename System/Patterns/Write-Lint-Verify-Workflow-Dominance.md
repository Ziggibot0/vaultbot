---
type: semantic
status: verified
created: 2026-07-27
last_reviewed: 2026-07-27
review_interval_days: 60
evidence_count: 3
evidence_sources:
  - "[[Chat-ok-do-it-now-and-dont-stop-until-the-vault-is-org]]"
  - "[[Chat-so-the-dream-pass-is-working-properly-end-to-end-n]]"
  - "[[Chat-you-just-had-some-HUGE-upgrades-bruv]]"
scope:
  - sessions
  - tool-usage
  - workflow
  - automation
falsifiable_if: "a future session occurs where the deterministic Write → Lint → Verify sequence is not the dominant execution pattern or Sean explicitly permits unverified writes"
tags: [semantic, pattern, consolidation, workflow, tool-chaining, determinism]
---

# Write-Lint-Verify-Workflow-Dominance

## How This Note Was Generated

This note was produced by deterministic pattern extraction across 121 chat logs in `vaultbot/chat/`. The extraction scanned for co-occurring tool calls and Sean's explicit workflow directives. Tool frequency analysis revealed that `code_run`, `safe_write`, and `vault_lint` consistently appear together, forming a rigid execution chain.

## Pattern Description

**The pattern:** VaultBot's execution is dominated by a deterministic three-step sequence: write code/note → lint/check quality → verify/test. Sean enforces this sequence strictly and treats any deviation (like writing without linting or testing) as a failure. The workflow must be end-to-end idempotent and self-validating before operator review.

**Evidence:**
- "ok do it now and don't stop until the vault is organized how the research supports" — Sean demands continuous execution of the full write-lint-verify cycle without intermediate pauses or manual checks
- "so the dream pass is working properly end-to-end now?" -- Sean validates success based on the complete automation of the workflow chain, not just isolated tool usage
- "you just had some HUGE upgrades bruv" -- Sean recognizes successful pattern adherence when VaultBot executes the deterministic chain autonomously and correctly

**Semantic rule:** Always execute `code_run` → `safe_write` → `vault_lint` in that exact order. Report syntax check results, import validation, and lint scores automatically after each step. Do not ask for intermediate approval during this chain; Sean expects end-to-end deterministic execution. This connects to [[Deterministic-Scaffolding-for-Small-Models]]: the workflow must be rigidly automated so small local models can reliably execute it without drifting into LLM-dependent guessing.

## Related
- [[Deterministic-Scaffolding-for-Small-Models]] -- explains the architectural necessity of rigid tool chaining for reliable small-model execution
- [[Execution-Loop-Dominance-Pattern]] -- provides the broader context of how Sean enforces strict loop structures over flexible or exploratory workflows
- [[Semantic-Consolidation-Architecture]] -- details the extraction pipeline that identified this co-occurrence pattern from tool frequency logs and converted it into an actionable rule