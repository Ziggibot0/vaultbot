---
type: exemplar
exemplar: procedural-note
exemplar_procedure: true
status: experimental
baseline: true
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0
falsifiable_if: a research note citing sources selected by this procedure is rejected by the operator or fails fact-checking
applies_to:
  - research
  - source-evaluation
depends_on:
  - "[[vaultbot/Structure-Research-Note]]"
  - "[[No-Wikipedia-Directive]]"
sources:
  - "https://ohiostate.pressbooks.pub/choosingsources/chapter/evaluating-websites/"
  - "https://www.onlinecolleges.net/for-students/online-academic-research/"
summary: SUMMARY
tags:
  - exemplar
  - exemplars
---

<!-- EXEMPLAR ANNOTATION: PROCEDURAL NOTE
     This note is an exemplar for writing procedural notes. A procedural note:
     1. Has full procedural schema in frontmatter (type, status, falsifiable_if, applies_to, depends_on, sources)
     2. Starts with 'When to Use This' — clear scope of when the procedure applies
     3. Has numbered steps, each starting with a bold action verb
     4. Each step is independently actionable — no step requires reading another step to understand
     5. Ends with a grading/decision section that produces a deterministic output
     6. Links to related procedures and directives via wikilinks
     7. Has a falsifiable_if clause — the procedure can be proven wrong
 -->

# How to Evaluate Source Credibility

<!-- ANNOTATION: Start with clear scope. When does this procedure apply? What does it cover? What does it NOT cover? This prevents the model from applying the procedure in the wrong context. -->
## When to Use This

Use this procedure when the research engine returns sources and you need to decide which to trust and cite. This applies to:
- Evaluating sources returned by `vault_research`
- Deciding which sources to include in a research note's synthesis
- Assessing whether a source is strong enough to support a claim

<!-- ANNOTATION: Each step is numbered and starts with a bold action verb. The steps are sequential — follow them in order. Each step is self-contained: no step requires reading another step to understand what to do. -->
## Steps

1. **Check authority.** Who wrote this? What are their credentials? Is the publisher reputable (academic press, government, established organization)? If you can't identify the author or publisher, the source is weak.

2. **Check corroboration.** Does the claim appear in multiple independent sources? The research engine already does this — if a fact appears in 2+ sources, it's corroborated. Single-source claims should be flagged as "unverified."

3. **Check currency.** Is the source recent enough for the topic? For fast-moving topics (AI, tech), prefer sources from the last 2 years. For stable topics (math, physics), older sources are fine.

4. **Check purpose.** Why was this published? Is it informing, selling, persuading, or entertaining? Sources that exist to sell or persuade are lower credibility than those that exist to inform.

5. **Apply lateral reading.** Don't just read the source itself — open a new tab and search for what OTHER sources say about this source. This is the method professional fact-checkers use (Stanford 2017 study). If other reputable sources trust it, that's a strong signal.

6. **Assign a grade.** A (strong), B (good), C (usable but weak), D (poor), F (unusable). Prefer A and B sources. C sources are acceptable for corroboration but not as sole support for a claim.

## Decision Points

- **If a source is on the [[No-Wikipedia-Directive|blocked list]]:** Skip it entirely. No evaluation needed.
- **If a source has no identifiable author:** Grade C or lower. Only use for corroboration, never as sole support.
- **If sources contradict each other:** Note the contradiction in the research note. Don't pick one and hide the other. Let the reader see the tension.
- **If all sources are grade C or lower:** Flag the research note as low-confidence. The synthesis may be unreliable.

## Validation Criteria

Source evaluation is done correctly when:
- ✅ Every source cited in a research note has been through the 6 steps
- ✅ Sources are graded and the grade influences how they're used in synthesis
- ✅ Single-source claims are flagged as "unverified"
- ✅ Contradictions between sources are noted, not hidden
- ✅ No blocked sources (Wikipedia) are used

## Common Failure Modes

1. **Citing the first source found** — no evaluation. Fix: run through the 6 steps before citing.
2. **Trusting authority without corroboration** — one authoritative source can still be wrong. Fix: require at least 2 sources for any claim.
3. **Ignoring currency** — citing a 2015 source for a 2026 AI topic. Fix: check the date.
4. **Not checking purpose** — citing a marketing page as a research source. Fix: check why the source exists.
5. **No lateral reading** — trusting a source based on its own claims. Fix: search for what others say about it.

## The Stanford Finding

A 2017 Stanford University study compared how university students, faculty, and professional fact-checkers evaluated sources. The result: fact-checkers were dramatically more accurate because they used **lateral reading** — leaving the source to verify it externally — while students and faculty stayed on the page and evaluated it internally. The lesson: don't trust a source's self-description. Verify externally.

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] — the full plan this procedure is part of
- [[vaultbot/Structure-Research-Note]] — the next step after source evaluation
- [[No-Wikipedia-Directive]] — blocked sources
- [[vaultbot/Vault-Knowledge-Only-Directive]] — vault is the only knowledge source


LOCKED
