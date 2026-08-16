---
created: 2026-07-26
summary: How to verify that synthesized claims in vault notes match their cited sources — the post-generation verification layer for VaultBot's research pipeline.
tags:
  - architecture
  - verification
  - fact-checking
  - epistemology
  - rag
type: semantic
status: raw
baseline: true
---

# Claim Verification for Vault Notes

## The Problem

When `vault_research` completes, it produces a synthesized note with facts drawn from web sources. The synthesis step is where hallucinations enter — the LLM may subtly distort a source's meaning, over-generalize a finding, or invent a connection the source didn't make. Currently, the only quality gate is `vault_lint` (checks broken wikilinks and frontmatter) and the operator's manual review. Neither catches claim-level hallucinations.

This is the epistemological gap identified in [[Self-Assessment-Using-the-Knowledge-Triad]]: the vault has no mechanism to verify that synthesized claims are *faithful* to their sources.

## What the Research Says

The field has converged on a three-stage pipeline for automated fact-checking [sources: Hallucination to Truth: A Review of Fact-Checking and Factuality Evaluation in Large Language Models, Claim Verification in the Age of Large Language Models: A Survey]:

1. **Claim extraction** — Break the generated text into atomic, verifiable claims. Microsoft's **Claimify** [sources: Claimify: Extracting high-quality claims from language model outputs] does this by decomposing complex sentences into simple factual assertions.

2. **Evidence retrieval** — For each atomic claim, retrieve the relevant source passage. In VaultBot's case, the sources are already known (they're cited in the note), so this step is a lookup, not a search.

3. **Claim verification** — For each atomic claim, check whether the source passage *entails* the claim. **MiniCheck** [sources: MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents] does this efficiently using grounded entailment models. **Chain-of-Verification** [sources: Chain-of-Verification Reduces Hallucination in Large Language Models] has the model self-generate verification questions and answer them against the source.

**OpenFactCheck** [sources: OpenFactCheck: A Unified Framework for Factuality Evaluation of LLMs] provides a unified framework combining all three stages and is open-source.

## How This Applies to VaultBot

The research pipeline already has the pieces — it just needs a verification layer between synthesis and storage:

```
vault_research -> scrape sources -> extract facts -> synthesize note
                                                        |
                                              [NEW: claim verification]
                                                        |
                                              pass -> write note to vault
                                              fail -> flag claims for re-synthesis
```

### The Verification Procedure

1. After `vault_research` writes a note, extract atomic claims from the synthesis section.
2. For each claim, locate the cited source (already archived in `learningMaterial/web/`).
3. Check whether the source passage entails the claim. This can be done:
   - **Deterministically** (string matching, NLI model) for simple factual claims
   - **Via LLM** for complex claims — but the LLM only sees the source passage + the claim, not the full note, reducing hallucination risk
4. Claims that fail verification are flagged in the note with a warning marker.
5. the operator's review of flagged claims becomes calibration data (see [[Calibration-via-Operator-Feedback]]).

### Why This Matters for Small Models

A 30B model is more likely to hallucinate during synthesis than a frontier model. The verification layer is **deterministic scaffolding** (see [[Deterministic-Scaffolding-for-Small-Models]]) — it catches hallucinations regardless of model size. The model proposes; the framework disposes.

## What Needs to Be Built

- A `claim_verifier.py` module that:
  - Extracts atomic claims from a note's synthesis section
  - Loads the cited source from `learningMaterial/web/`
  - Checks entailment (start with simple string matching, upgrade to NLI model)
  - Writes verification results to the note's frontmatter
- Integration with `vault_research` to run verification automatically after synthesis
- A `vault_lint` extension to check for unverified claims

## Related
- [[Deterministic-Scaffolding-for-Small-Models]] — the sandwich pattern this fits into
- [[How-to-Evaluate-Source-Credibility]] — pre-synthesis source evaluation (this is post-synthesis)
- [[Calibration-via-Operator-Feedback]] — using the operator's corrections to calibrate verification
- [[Procedural-Bootstrap-and-Evolution-Plan]] — where this fits in the evolution roadmap
- [[vaultbot_stuff/Vault-Knowledge-Only-Directive]] — why provenance matters
- [[Self-Assessment-Using-the-Knowledge-Triad]] — the gap this fills
