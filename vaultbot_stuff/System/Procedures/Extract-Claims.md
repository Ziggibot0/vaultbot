---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-03
description: Extract factual claims from a text passage. Returns a JSON list of claims, each with the claim text and the supporting span from the source. Uses the small model — claim extraction is structured extraction, not reasoning.
when_to_use: when you need to pull discrete claims out of a source text for later verification via Check-Entailment
falsifiable_if: the extracted claims are not actually present in or derivable from the source text
applies_to:
  - claim-extraction
  - research-quality
  - fact-checking
  - verify-claims
allowed_tools:
  - llm_generate
summary: Extract-Claims
tags:
  - procedure
  - procedures
---

# Extract-Claims

## When to Run This

Run this when you need to pull discrete factual claims out of a source text so they can be individually verified later. This is the first step of the Verify-Claims workflow: extract claims, then check each one against its source via [[Check-Entailment]].

## Steps

### Step 1: Ask the small model to extract claims

1. [llm: You are a claim extraction system. Given a source text, extract all factual claims made in the text. For each claim, return a JSON object with: "claim" (the claim stated as a single sentence), "span" (the exact text span from the source that supports this claim). Return a JSON array of these objects. Only extract claims that are actually present in or directly derivable from the text — do not fabricate.]

## Related

- [[Check-Entailment]] — verify each extracted claim against its source
- [[Verify-Claims]] — the full workflow that chains Extract-Claims + Check-Entailment