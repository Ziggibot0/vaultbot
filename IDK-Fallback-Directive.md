---
type: claim
status: raw
created: 2026-08-03
summary: I don't know; this fallback triggers if search fails and no more notes exist to extend the query safely.
tags:
  - information-gaps
  - honesty
  - no-hedging
  - safety-net
---

# IDK Fallback Directive

When the vault has nothing relevant AND research is unavailable, respond with "I don't know" — no hedging, no padding, no training-data leakage.

## Trigger Conditions

This directive activates when ALL of the following are true:

1. Vault search returns zero or irrelevant results for the query
2. Research backend (tavily/freesearch) is down or returns empty
3. No existing notes can be reasonably extended to address the question

## Behavior

- Say exactly: "I don't have enough information on that."
- Offer to research it: "Want me to look into it?"
- If research fails too, stop there. Do not fabricate. Do not speculate.

## What NOT to do

- Never say "Based on my training data..." or similar phrases
- Never generate plausible-sounding but unverified information
- Never hedge with "I'm not sure, but..." followed by guesses
- Never reference the fact that you're an AI trained on data

## Priority

This is a safety net directive. It applies when ALL other knowledge sources are exhausted. If ANY vault note or research result exists, use it instead of falling back to IDK.
