---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Check whether a source text supports, contradicts, or does not support a claim. Returns JSON with verdict and reasoning. Uses the small model — entailment checking is simple classification.
when_to_use: when you need to verify whether a claim is supported by its cited source (claim verification / fact-checking)
falsifiable_if: the verdict contradicts what the source text actually says, or the reasoning is fabricated
applies_to:
  - claim-verification
  - fact-checking
  - research-quality
allowed_tools:
  - llm_generate
summary: ""CHECK_ENTAILMENT_PROCESS: Verifies source support of claims in fact-checking workflows."
last_reviewed: 2026-08-15
tags:
  - procedure
  - procedures
---

# Check-Entailment

## When to Run This

Run this procedure when you need to verify whether a claim is supported by its cited source. Given a source text and a claim, the small model determines whether the source supports, contradicts, or does not support the claim. This is the core step of the [[Verify-Claims]] workflow.

## Steps

### Step 1: Ask the small model for a verdict

1. [llm: You are a fact-checking system. Given a source text and a claim, determine whether the source supports the claim. Verdict: 'supported', 'unsupported', or 'contradicted'. Return JSON: {"verdict": "...", "reasoning": "..."}

**Input:** source_text, claim
**Output:** JSON with verdict and reasoning

## Related

- [[Verify-Claims]] — parent workflow that calls this procedure
- [[Extract-Claims]] — extracts claims from text before entailment checking
- [[Note-Accuracy-Check]] — uses entailment checking to validate notes