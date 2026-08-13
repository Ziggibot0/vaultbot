---
type: semantic
status: verified
baseline: true
created: 2026-07-27
last_reviewed: 2026-07-27
review_interval_days: 60
evidence_count: 3
evidence_sources:
  - "[[Chat-that-was-way-too-much-and-tbh-i-didnt-read-it-ju]]"
  - "[[Chat-you-got-all-that-from-the-textbook-and-you-didnt]]"
  - "[[Chat-ok-so-youre-all-done-now]]"
scope:
  - sessions
  - communication
  - reporting-format
  - sean-preferences
falsifiable_if: a future session occurs where the operator explicitly requests detailed, wall-of-text reports or stops rejecting verbose outputs
tags:
  - semantic
  - pattern
  - consolidation
  - sean-preferences
  - communication
  - brevity
summary: Over-Reporting-Brevity-Rule
---

# Over-Reporting-Brevity-Rule

## How This Note Was Generated

This note was produced by deterministic pattern extraction across 75 chat logs in `vaultbot/chat/`. The extraction scanned for exchange lengths exceeding 2000 characters and cross-referenced them with the operator's negative sentiment keywords to identify over-reporting as a recurring friction point. No LLM was used for pattern detection — only for prose synthesis of the pre-extracted findings.

## Pattern 1: Over-Reporting Rejection (3 instances)

**The pattern:** VaultBot tends to produce walls of text when the operator wants bottom-line-up-front. Verbose outputs are consistently rejected or skimmed past by the operator, who explicitly demands brevity and key info extraction.

**Evidence:**
- "that was way too much and tbh i didn't read it. just think about how you can get the key info" -- the operator directly rejects verbose reporting and requests compression
- "you got all that from the textbook and you didn't fill in ANY of that with your trained knowledge? that's just a pretty generic thing to go to. could " -- the operator flags regurgitation over synthesis, implying unnecessary length
- "ok so you're all done now?" -- the operator cuts off long procedural dumps by asking for completion status, preferring concise milestone reporting

**Semantic rule:** Lead with what was done in 1-2 sentences. Follow with a table or bullet list of key actions/metrics. Provide detailed explanations only if the operator explicitly asks. This aligns with [[the operator-Communication-Preferences]] which mandates keeping outputs short and reporting accomplishments rather than regurgitating process logs.

**Prevention:** Implement a length cap on session summaries (max 300 words). Use structured lists for status updates. Auto-flag any draft response exceeding the cap before sending it to the operator.

## Related
- [[the operator-Communication-Preferences]] -- defines the operator's explicit mandate for brevity and bottom-line-up-front reporting
- [[Cross-Session-Patterns-from-75-Chat-Logs]] -- documents the historical frequency of over-reporting friction across early development sessions
- [[Calibration-via-Operator-Feedback]] -- tracks how the operator's negative sentiment toward verbose outputs drives iterative communication refinement