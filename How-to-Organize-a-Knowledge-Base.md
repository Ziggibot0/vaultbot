---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "the vault becomes difficult to navigate, notes can't be found by FUSED retrieval, or Sean reports that the vault is cluttered or disorganized"
applies_to:
  - vault-maintenance
  - note-organization
  - knowledge-management
depends_on:
  - "[[How-to-Structure-a-Research-Note]]"
  - "[[Vault-Knowledge-Only-Directive]]"
  - "[[Pre-Thought-Information-Shapes]]"
sources:
  - "https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f"
  - "https://dev.to/penfieldlabs/what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988"
---

# How to Organize a Knowledge Base

## When to Use This

Use this procedure when:
- Creating a new note and deciding where it belongs
- Deciding whether to create a new note or append to an existing one
- Cleaning up or reorganizing the vault
- Deciding on naming, tagging, and linking conventions

## Steps

### Step 1: Decide — New Note or Append?

**Create a new note when:**
- The topic is distinct enough to warrant its own retrievable unit
- The note will be referenced by multiple other notes (high fan-in)
- The content is self-contained — it makes sense on its own without requiring another note for context

**Append to an existing note when:**
- The new information is an update or extension of the existing note's topic
- The existing note is thin (under 200 words) and the new information fills it out
- The new information doesn't change the note's core thesis, just adds detail

**Rule of thumb:** If you'd search for this topic independently, it needs its own note. If you'd only find it by looking at the related note, append.

### Step 2: Name the Note

- **Use descriptive titles** — "How-to-Evaluate-Source-Credibility" not "Source Eval" or "Note1"
- **Use title case with hyphens** — consistent with existing vault conventions
- **Include the topic and type** — "How-to-X" for procedures, "X-for-Y" for architecture, just "X" for concepts
- **Avoid dates in titles** — dates are in frontmatter, not filenames (except journal entries which are sacred)

### Step 3: Add Frontmatter

Every note gets YAML frontmatter:
```yaml
---
created: 2026-07-26
summary: "One sentence describing what this note is about"
tags: [relevant, tags, here]
---
```

Procedural notes get the full procedural schema (see [[How-to-Structure-a-Research-Note]] for the template).

### Step 4: Link to Related Notes

- **Link outward** — Add wikilinks to notes that provide context, dependencies, or related concepts
- **Link inward** — Check if other notes should link TO this new note. Use `vault_search` to find related notes and add links from them
- **Explain the link** — The prose around a wikilink should explain WHY the connection matters, not just that it exists. This is the [[Pre-Thought-Information-Shapes]] principle: the reasoning lives in the prose

### Step 5: Tag Appropriately

- Tags should describe the note's TYPE and DOMAIN, not its content
- Good tags: `architecture`, `procedure`, `exemplar`, `philosophy`, `research`
- Bad tags: `interesting`, `important`, `todo`
- Keep tags to 2-5 per note

### Step 6: Verify with vault_lint

Run `vault_lint` on the new note. Fix any issues:
- Broken wikilinks → fix the link target or remove the link
- Missing frontmatter → add it
- Argument quality issues → add more reasoning or context

## Organization Principles

### Notes Are Atomic
Each note should express ONE core idea. If a note tries to do three things, split it into three notes and link them together. Atomic notes are easier to retrieve, easier to link, and easier to update.

### The Graph Does the Thinking
The vault's power comes from connections, not individual notes. A note with 10 wikilinks is more valuable than a note with 10 paragraphs, because the links enable FUSED retrieval to find related concepts. When in doubt, link rather than duplicate.

### Prose Explains Relationships
A wikilink says "these are connected." The prose around it says HOW and WHY. Never write a bare wikilink without surrounding context. This is the [[Vault-Thinks-LLM-Synthesizes]] principle — the vault stores the reasoning, the LLM narrates it.

### Avoid Orphan Notes
Every note should link to at least one other note and be linked from at least one other note. Orphan notes are invisible to graph-based retrieval. If you create a note, make sure it's connected.

## Common Failure Modes

| Failure | What happens | How to fix |
|---|---|---|
| **Mega-notes** | One note tries to cover everything, becomes unmaintainable | Split into atomic notes, link them together |
| **Orphan notes** | Note has no links in or out, can't be found by FUSED | Add links to related notes, search for related content |
| **Vague titles** | "Notes" or "Stuff" — can't be found by search | Rename to descriptive title |
| **Duplicate notes** | Two notes cover the same topic | Consolidate into one, redirect links |
| **Broken wikilinks** | Links to deleted or renamed notes | Run vault_lint, fix or remove broken links |

## Validation Criteria

This procedure is working correctly when:
- New notes are findable by `vault_search` within one search
- Notes don't duplicate content that exists elsewhere in the vault
- `vault_lint` passes on new notes (0 broken links, frontmatter present)
- The vault graph stays connected (no orphan islands)

## Related

- [[How-to-Structure-a-Research-Note]] — how to write the content of a research note
- [[How-to-Evaluate-Source-Credibility]] — how to evaluate sources before citing them
- [[Pre-Thought-Information-Shapes]] — why connections encode reasoning
- [[Vault-Longevity-Architecture]] — why the vault is the mind
- [[Exemplar-Note-Design]] — how to design exemplar notes for pattern-matching
- [[Vault-Thinks-LLM-Synthesizes]] — prose explains relationships, not metadata
