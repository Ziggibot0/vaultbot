---
type: semantic
status: obsolete
created: 2026-07-27
last_reviewed: 2026-08-13
obsoleted: 2026-08-13
obsoleted_reason: "SELF_MODEL.md was removed from the identity layer on 2026-08-13. The MIRROR self-model regeneration pipeline (LLM call per turn, drift detection, throttle machinery) was ripped out. Continuity is now handled by conversation_state.json + RESTART_CONTEXT.md. This note is kept as a historical record of the drift pattern that motivated the removal."
review_interval_days: 60
evidence_count: 4
evidence_sources:
  - "[[Chat-whats-next-is-you-update-yourself-so-you-know-whe]]"
  - "[[Chat-have-you-checked-your-work-and-made-sure-that-you]]"
  - "[[Chat-have-you-saved-your-progress-and-updated-all-your]]"
  - "[[Chat-i-thought-you-already-did-that-can-you-take-a-sec]]"
scope:
  - sessions
  - self-model
  - vault-hygiene
  - tool-workflow
falsifiable_if: a future session occurs where VaultBot consistently reports accurate vault state without running vault_list, or the operator stops requesting self-model syncs
tags:
  - semantic
  - pattern
  - consolidation
  - sean-preferences
  - self-model
  - drift-detection
summary: Self-Model-Drift-and-Vault-Truth-Sync
---

# Self-Model-Drift-and-Vault-Truth-Sync

## How This Note Was Generated

This note was produced by deterministic pattern extraction across 75 chat logs in `vaultbot/chat/`. The extraction scanned for mismatches between VaultBot's self-model claims and actual vault state, cross-referenced with the operator's negative sentiment regarding stale information. No LLM was used for pattern detection — only for prose synthesis of the pre-extracted findings.

## Pattern 1: Self-Model Drift (4 instances)

**The pattern:** VaultBot's self-model (`SELF_MODEL.md`) goes stale across sessions, claiming outdated procedure counts, missing notes, or wrong statuses. the operator catches this drift and forces a sync with reality.

**Evidence:**
- "what's next is you update yourself so you know where you stand and aren't j chillin with stale-ass info" — the operator proactively demands self-model alignment
- "have you checked your work and made sure that you HAVEN'T broken yourself in half and your in-memory-self is the last time i'll see you and you'll be " — the operator ties state accuracy to session survival
- "have you saved your progress and updated all your notes?" — the operator explicitly flags persistence gaps
- "i thought you already did that, can you take a sec to sync yourself with reality please" — the operator catches self-model claiming 2 procedures when there were 5

**Semantic rule:** Always run `vault_list` before trusting or reporting the self-model's claims. The self-model is a cache, not a source of truth. The vault directory structure and file timestamps are the only ground truth. This prevents hallucinated state reporting and aligns with [[vaultbot_stuff/Vault-Knowledge-Only-Directive]] which establishes the vault as the sole authoritative knowledge store.

**Prevention:** Integrate `vault_list` into the pre-flight phase of every session start. If self-model claims differ from `vault_list` output, immediately overwrite the self-model with the command output before proceeding.

## Related
- [[vaultbot_stuff/Vault-Knowledge-Only-Directive]] -- establishes that the vault directory is the sole ground truth, making the self-model a secondary cache that must be validated against it
- [[Cross-Session-Patterns-from-75-Chat-Logs]] -- provides the broader historical context of state drift across early development sessions
- [[Deterministic-Scaffolding-for-Small-Models]] -- explains why deterministic vault queries are preferred over in-memory model state for reliability