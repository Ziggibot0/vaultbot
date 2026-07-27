---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "a note that passes this verification procedure is later found to contain a hallucinated or unsupported claim"
applies_to:
  - research
  - note-writing
  - verification
depends_on:
  - "[[How-to-Structure-a-Research-Note]]"
  - "[[How-to-Evaluate-Source-Credibility]]"
  - "[[Claim-Verification-for-Vault-Notes]]"
sources:
  - "https://arxiv.org/html/2408.14317v1"
  - "https://aclanthology.org/2024.findings-acl.212.pdf"
  - "https://www.microsoft.com/en-us/research/blog/claimify-extracting-high-quality-claims-from-language-model-outputs/"
  - "https://aclanthology.org/2024.emnlp-main.499/"
---

# How to Verify Claims in a Research Note

## When to Use This

Use this procedure after `vault_research` has written a note and before it is considered final. This applies to:
- Notes created from autonomous background research
- Notes created from on-demand research
- Any note that synthesizes web sources into knowledge claims

Do NOT use this for:
- Chat logs (conversation records, not knowledge claims)
- Directive notes (policy, not research)
- Textbook index notes (tables of contents, not synthesis)

## Steps

1. **Extract atomic claims from the synthesis section.** Read the synthesis prose and break it into individual factual assertions. Each claim should be a single sentence that can be independently verified. For example, "FILCO filters irrelevant spans from retrieved passages before generation [sources: ...]" is one atomic claim.

2. **Locate the cited source for each claim.** Each claim should have an inline source citation like `[sources: Source Title]`. Find the archived source in `learningMaterial/web/`. If no source is cited, flag the claim as unsourced.

3. **Check entailment.** For each atomic claim, read the relevant section of the source and check: does the source *say* what the claim asserts? Look for:
   - **Direct support** — the source explicitly states the claim
   - **Indirect support** — the source implies the claim through evidence that clearly leads to it
   - **No support** — the source doesn't say this, or says something different
   - **Contradiction** — the source says the opposite

4. **Flag failures.** Claims with no support or contradiction get a warning marker in the note. Claims with no source citation get an unsourced marker.

5. **Log verification results.** Record the verification outcome in the note's frontmatter:
   ```yaml
   verification:
     total_claims: 12
     verified: 10
     unverified: 1
     unsourced: 1
   ```

6. **Report to Sean.** If any claims are unverified or unsourced, mention it when reporting the note. Don't silently pass a note with verification failures.

## Falsifiability

This procedure is falsifiable: if a note passes all verification steps but Sean later finds a hallucinated or unsupported claim, the procedure failed. Log it as a failure in the procedure tracker.

## Related
- [[Claim-Verification-for-Vault-Notes]] — the architecture this procedure implements
- [[How-to-Structure-a-Research-Note]] — prerequisite (write the note first)
- [[How-to-Evaluate-Source-Credibility]] — pre-synthesis source evaluation
- [[Calibration-via-Operator-Feedback]] — using Sean's corrections to calibrate this procedure
- [[Procedural-Bootstrap-and-Evolution-Plan]] — where this fits in the evolution roadmap
