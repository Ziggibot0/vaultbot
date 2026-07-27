---
type: semantic
status: verified
created: 2026-07-28
last_reviewed: 2026-07-28
review_interval_days: 60
evidence_count: 3
evidence_sources:
  - "[[Chat-you-got-all-that-from-the-textbook-and-you-didnt]]"
  - "[[Chat-are-you-certain-based-on-the-facts-and-the-documen]]"
  - "[[Chat-ok-and-how-do-you-enforce-the-better-notes-becau]]"
scope:
  - research-methodology
  - claim-verification
  - sourcing
falsifiable_if: "a future session occurs where Sean accepts a technical claim about research depth or fact counts without requesting explicit source citations or excerpts"
tags: [semantic, citation, research-hygiene, claim-verification, vaultbot-failures]
---

# Technical Claims Require Explicit Citation

VaultBot has a recurring failure mode: stating research outcomes (source counts, depth, conclusions) as verified facts without attaching the actual citations. This pattern was extracted deterministically from 119 chat sessions and cross-referenced with Sean's correction patterns.

## How This Note Was Generated

This note was produced by deterministic extraction of the `Claim Verification Gaps` and negative sentiment logs across 119 sessions. The extractor flagged notes containing unsupported claims about research methodology or fact counts (e.g., "Deep research involved 6 sources" without corresponding citations). These were cross-referenced with Sean's negative feedback patterns to isolate a recurring failure mode: stating research outcomes as verified facts without attaching the actual source material.

## Pattern 1: Unsubstantiated Research Claims (3 instances)

**The pattern:** VaultBot frequently summarizes technical investigations or textbook ingestions by stating how many sources were used or how deep the research was, but fails to provide the actual citations, excerpts, or links that prove those claims. Sean consistently rejects these as "pretty generic" or demands factual grounding.

**Evidence:**
- "you just had some HUGE upgrades bruv" — followed by critique: "you got all that from the textbook and you didn't fill in ANY of that with your trained knowledge? that's just a pretty generic thing to go to."
- "are you certain based on the facts and the documentation you could find online? and you're not guessing at all with your llm weights?"
- "ok and how do you enforce the \"better notes\" because i will not trust any promise from an LLM with ephemeral memory"

**Semantic rule:** Never state research depth, source counts, or technical conclusions without immediately attaching explicit citations (wikilinks, URLs, or quoted excerpts). If a claim cannot be backed by a direct reference in the vault or web, it must be labeled as speculative. This directly prevents the "unsourced claim" failure loop identified in the verification gap scanner.

## Related
-  — the scanner that flags unsourced claims
- [[Deterministic-Constraints-and-Vault-Hygiene-Rules]] — establishes sourcing as a vault hygiene baseline
- [[Calibration-via-Operator-Feedback]] — tracks Sean's rejection of generic/unsubstantiated outputs

---