---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "a note produced by following these steps fails vault_lint or Sean's review"
applies_to:
  - research
  - note-writing
depends_on:
  - "[[IDK-Fallback-Directive]]"
  - "[[Vault-Knowledge-Only-Directive]]"
sources:
  - "https://zenkit.com/en/blog/a-beginners-guide-to-the-zettelkasten-method/"
  - "https://forum.zettelkasten.de/discussion/2612/types-of-permanent-notes"
  - "https://affine.pro/blog/zettelkasten-method"
  - "https://www.apragmaticmind.com/blog/zettelkasten-method"
  - "https://www.fabriziomusacchio.com/blog/2022-06-17-zettelkasten/"
---

# How to Structure a Research Note

## When to Use This

Use this procedure when writing a permanent research note after `vault_research` completes. This applies to:
- Notes created from autonomous background research
- Notes created from on-demand research
- Any note that synthesizes web sources into a knowledge claim

Do NOT use this for:
- Chat logs (those are conversation records, not knowledge)
- Directive notes (those are policy, not research)
- Textbook index notes (those are tables of contents)

## Steps

1. **Write a one-sentence summary** at the top. State what the note is about and why it matters. This is the "elevator pitch" — if someone reads only this line, they should know what the note covers.

2. **Write the key findings as a bulleted list.** Each finding is one corroborated fact with its source cited inline as `[sources: Source Title]`. Group related findings together. Order by importance, not by source.

3. **Synthesize in prose below the findings.** Don't just list facts — explain how they connect. What's the argument? What's the pattern? The findings are the raw material; the synthesis is the thought. This is where the vault "thinks."

4. **Add wikilinks to related notes.** Every note should connect to at least one other note. Use `[[Note-Title]]` format. The link should be meaningful — the prose around it explains the relationship.

5. **Add frontmatter.** Include `created` date, `tags`, and `summary` fields. The summary should be a one-line description of the note's content.

6. **Run vault_lint.** Check for broken wikilinks, missing frontmatter, and argument quality. Fix any issues found.

## Decision Points

- **If the research returned fewer than 3 sources:** Flag the note as low-confidence in the summary. The synthesis may be thin.
- **If findings contradict each other:** Note the contradiction explicitly. Don't smooth it over. Use `contradicts::` typed edges if the [[Pre-Thought-Information-Shapes]] system is available.
- **If the topic is a procedure (how-to):** Use the procedural note schema instead (see [[Procedural-Bootstrap-and-Evolution-Plan]] Part 3). Procedural notes have different frontmatter and body structure.

## Validation Criteria

A well-structured research note passes when:
- ✅ Has frontmatter with `created`, `tags`, `summary`
- ✅ Has a one-sentence summary at the top
- ✅ Has at least 3 corroborated findings with sources cited
- ✅ Has prose synthesis (not just a fact list)
- ✅ Has at least 1 wikilink to a related note
- ✅ Passes `vault_lint` with 0 broken wikilinks
- ✅ Is self-contained — a reader can understand it without reading other notes

## Common Failure Modes

1. **Fact dump** — listing facts without synthesis. Fix: add a "What this means" paragraph.
2. **No links** — isolated note with no wikilinks. Fix: search the vault for related notes and link them.
3. **Missing frontmatter** — note has no metadata. Fix: add the standard frontmatter block.
4. **Broken wikilinks** — links to notes that don't exist. Fix: run `vault_lint` and fix or escape broken links.
5. **Source not cited** — claim without a source. Fix: add `[sources: Source Title]` inline.

## Examples

A good research note looks like:

```
---
created: 2026-07-26
summary: "How Zettelkasten structures knowledge through atomic notes"
tags: [knowledge-management, zettelkasten, pkm]
---

# Zettelkasten Note Structure

## Summary
The Zettelkasten method structures knowledge as a network of atomic, 
self-contained notes linked by relationships...

## Key Findings
- Each permanent note contains one idea [sources: Zettelkasten Forum]
- Notes are written in your own words, not copied [sources: Musacchio]
...

## Synthesis
The pattern is fractal: each note is a complete thought, and the 
network of notes forms a larger argument. This connects to 
[[Fractal-Entropy-Principle]] because...
```

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] — the full plan this procedure is part of
- [[Pre-Thought-Information-Shapes]] — how note structure encodes reasoning
- [[Vault-Knowledge-Only-Directive]] — vault is the only knowledge source
- [[IDK-Fallback-Directive]] — what to do when research fails
