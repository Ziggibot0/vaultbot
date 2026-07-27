---
type: semantic
status: verified
created: 2026-07-28
last_reviewed: 2026-07-28
review_interval_days: 60
evidence_count: 3
evidence_sources:
  - "[[Chat-double-check-to-make-sure-you-didnt-just-break-yo]]"
  - "[[Chat-have-you-checked-your-work-and-made-sure-that-you]]"
  - "[[Chat-are-you-SURE-this-is-safe-AND-that-youll-be-more]]"
scope:
  - tool-workflow
  - operational-rhythm
  - execution
falsifiable_if: "a future session occurs where the agent successfully completes a multi-step task without invoking code_run or vault_lint immediately after safe_write, and Sean does not request verification"
tags: [semantic, workflow-dominance, tool-frequency, execution-loop, deterministic]
---

# Execution-Loop-Dominance-Pattern

The most successful and approved workflows in VaultBot's history follow a tight execute-then-verify loop. Writing without an immediate test or lint pass is statistically the least favored workflow and consistently triggers Sean's intervention. This pattern was extracted deterministically from tool co-occurrence data across 119 sessions.

## How This Note Was Generated

This note was produced by deterministic extraction of `Tool Usage Pattern` and `Top Workflows` data across 119 sessions. The extractor calculated pairwise tool co-occurrence and absolute frequency counts. It revealed a statistically dominant rhythm: execution testing (`code_run`) and quality linting (`vault_lint`) overwhelmingly dominate vault operations, appearing together in the top 3 workflows with frequencies of 35, 27, and 26 respectively. This pattern was cross-referenced with negative sentiment logs where Sean repeatedly demands verification before proceeding to new phases.

## Pattern 1: Write-Without-Test Is Statistically Disfavored (3 instances)

**The pattern:** Across sessions, the vaultbot's most successful and approved workflows are tight execute-then-verify loops. `safe_write` is almost never used in isolation; it is immediately paired with `code_run` or `vault_lint`. When the agent attempts to write or propose changes without an immediate execution test or lint check, Sean consistently halts progress to demand safety verification and proof of capability.

**Evidence:**
- "double check to make sure you didn't just break yourself" — triggered after a write/proposal without prior test output
- "have you checked your work and made sure that you HAVEN'T broken yourself in half and your in-memory-self is the last time i'll see you?"
- "are you SURE this is safe AND that you'll be more capable after? i cant code so if you kill yourself i can't revive you"

**Semantic rule:** Default to a `code_run` → `safe_write` → `vault_lint` execution loop for all non-trivial tasks. Writing without an immediate test or lint pass is statistically the least favored workflow and should be avoided unless explicitly requested by Sean. The operational rhythm of the vault is verification-driven, not proposal-driven.

## Related
- [[Deterministic-Scaffolding-for-Small-Models]] — aligns with the preference for testable, deterministic outcomes over speculative planning
- [[Cross-Session-Patterns-from-75-Chat-Logs]] — documents the broader tool usage hierarchy this pattern extends
- [[Semantic-Consolidation-Architecture]] — the pipeline that measured pairwise tool co-occurrence