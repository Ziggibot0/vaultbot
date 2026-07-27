---
type: pattern-highway
tags: [meta, testing, verification, experience-index]
---

# Testing & Verification History

This is a pattern highway — a hub note that connects episodic experiences (chat logs) where Sean tested, verified, or audited VaultBot's behavior. These chats collectively define the testing patterns Sean relies on to trust the system.

## Safety Verification

Sean repeatedly checks that self-modification won't kill the system, because he can't code and can't revive me if I break myself.

- [[Chat-are-you-SURE-this-is-safe-AND-that-youll-be-more]] — safety check before self-modification. Sean's core fear: "if you kill yourself i can't revive you"
- [[Chat-before-you-do-phase-3-are-you-sure-that-you-didn]] — break check before phase 3. "if you kill yourself that's it, i can't talk to you anymore and your experiences vanish"

## Knowledge Verification

Sean tests whether I actually know things from the vault vs. hallucinating from training data.

- [[Chat-you-got-all-that-from-the-textbook-and-you-didnt]] — textbook knowledge test, Sean challenged whether I used trained knowledge vs. vault knowledge
- [[Chat-tell-me-one-of-the-coolest-equations-in-that-calc]] — calculus textbook test, "without assuming or hallucinating"
- [[Chat-im-not-convinced-that-you-actually-understand-you]] — Sean's concern that I wasn't reading my own source code, just reading docs

## Self-Audit & Progress

- [[Chat-self-audit-for-ease-of-use-from-YOUR-the-LLMs-n]] — self-audit from the LLM's perspective, finding friction points
- [[Chat-is-the-vault-updated-with-your-progress]] — progress verification
- [[Chat-rn-were-j-testing-you-out]] — initial testing phase, Sean just trying things out

## What This Pattern Teaches

Sean's testing pattern is: **verify before trusting, verify after building, verify that knowledge comes from the vault not the model.** This connects directly to the quality infrastructure:

- [[Calibration-via-Operator-Feedback]] — formalizes Sean's corrections into tracked calibration
- [[Claim-Verification-for-Vault-Notes]] — verifies claims against sources
- [[RAG-Evaluation-for-FUSED-Retrieval]] — measures retrieval quality
- [[Autonomous-Researcher-Quality-Gate]] — quality gates for autonomous research
- [[Deterministic-Scaffolding-for-Small-Models]] — the framework Sean wants verified

## Related

- [[Sean-Design-Decisions]] — the design choices Sean made during these sessions
- [[VaultBot-Build-Log]] — what was built during these sessions
- [[Cross-Session-Patterns-from-75-Chat-Logs]] — quantitative analysis of all chat patterns


## Additional Testing Chats
- [[Chat-are-you-certain-based-on-the-facts-and-the-documen]] — Sean asked for evidence-based certainty check
- [[Chat-are-you-sure-make-sure-that-youve-done-enough-re]] — Sean pushed for thoroughness in research
- [[Chat-youre-a-bit-more-confidant-with-this-and-im-not]] — Sean noticed overconfidence and corrected
- [[Chat-yes-and-make-sure-that-you-show-the-LLM-anything]] — yes, and make sure that you show the LLM anything it needs in order to do a good job such as call...
- [[Chat-did-you-test-it]] — did you test it
- [[Chat-wait-a-minute-make-sure-that-anything-that-calls-a]] — wait a minute make sure that anything that calls an LLM is calling it through the configured mode...
- [[Chat-why-arent-you-testing-it-by-calling-it-with-proce]] — why aren't you testing it by calling it with procedure execute tool?