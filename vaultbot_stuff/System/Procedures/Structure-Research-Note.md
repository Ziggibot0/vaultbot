---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-30
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
description: "Structure a research note after vault_research completes: write summary, key findings with sources, prose synthesis, wikilinks, frontmatter, then validate with vault_lint. 5 deterministic steps. Idempotent — safe to re-run (lint check skips already-valid notes)."
falsifiable_if: "a note produced by following these steps fails vault_lint or the operator's review"
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
allowed_tools:
  - vault_lint
  - vault_search
  - vault_list
---

# Structure-Research-Note

## When to Run This

Run this procedure when writing a permanent research note after `vault_research` completes. Applies to:
- Notes created from autonomous background research
- Notes created from on-demand research
- Any note that synthesizes web sources into knowledge claims

Do NOT use for: chat logs, directive notes, textbook index notes.

## Steps

### Step 1: Write Summary and Key Findings

Write a one-sentence summary at the top, then key findings as a bulleted list with inline source citations.

1. ```python
import json, os, datetime

today = datetime.date.today().isoformat()

# Template for a research note — the LLM fills in the actual content.
# NOTE: avoid using ## headers inside code blocks (procedure compiler parses them as section breaks)
template_structure = {
    "frontmatter": {
        "type": "research",
        "status": "raw",
        "created": today,
        "summary": "(one-line description of what the note covers and why it matters)",
        "tags": ["research", "(topic-specific-tags)"],
        "source_count": 0,
        "fact_count": 0,
    },
    "title": "# Title-Case-With-Hyphens",
    "summary_section": "One-sentence summary at the top — the elevator pitch",
    "key_findings": [
        "Each finding is one corroborated fact with [sources: Source Title] citation",
        "Group related findings together",
        "Order by importance, not by source",
    ],
    "synthesis": "Prose explaining how findings connect — the argument, the pattern, the thinking",
}

result = json.dumps({
    "status": "template_ready",
    "date": today,
    "template": template_structure,
    "action": "Fill in the template. Each finding must have an inline source citation. Group related findings. Order by importance.",
})
```

### Step 2: Write Prose Synthesis

Don't just list facts — explain how they connect. The synthesis is where the vault thinks.

2. ```python
import json

result = json.dumps({
    "status": "synthesis_required",
    "requirements": [
        "Explain the argument — what pattern emerges from the findings?",
        "Use reasoning language: because, therefore, which means, this suggests",
        "Connect to at least one other note using [[wikilinks]]",
        "If findings contradict each other, note the contradiction explicitly",
        "If fewer than 3 sources, flag as low-confidence in the summary",
    ],
    "anti_patterns": [
        "Fact dump — listing facts without synthesis",
        "Smooth-over — hiding contradictions between sources",
        "No links — isolated note with no wikilinks",
    ],
    "action": "Write the synthesis section. This is the thinking, not just the data.",
})
```

[validate: contains "synthesis_required"]

### Step 3: Add Wikilinks

Every note should connect to at least one other note. Use [[Note-Title]] format with meaningful prose context.

3. ```python
import json

result = json.dumps({
    "status": "links_required",
    "rules": [
        "At least 1 wikilink to a related note (minimum)",
        "Use [[Note-Title]] format without .md extension",
        "The prose around each link explains the relationship",
        "Only link to notes that actually exist — run vault_search to verify",
        "If no related notes exist, the note is isolated — search harder or create a bridge",
    ],
    "action": "Search the vault for related notes using vault_search. Add wikilinks with context sentences.",
})
```

### Step 4: Add Frontmatter

Include created date, tags, summary, and status fields.

4. ```python
import json, datetime

today = datetime.date.today().isoformat()

frontmatter_template = {
    "type": "research",
    "status": "raw",
    "created": today,
    "summary": "(one-line description)",
    "tags": ["research", "(topic-specific-tags)"],
    "source_count": 0,
    "fact_count": 0,
}

result = json.dumps({
    "status": "frontmatter_required",
    "template": frontmatter_template,
    "required_fields": ["type", "status", "created", "summary", "tags"],
    "optional_fields": ["source_count", "fact_count", "sources", "depends_on"],
    "action": "Ensure frontmatter is present and all required fields are filled.",
})
```

### Step 5: Validate with vault_lint

Run vault_lint to check for broken wikilinks, missing frontmatter, and argument quality. Fix any issues.

5. ```python
import json

result = json.dumps({
    "status": "lint_required",
    "checks": [
        "Has frontmatter with created, tags, summary",
        "Has at least 1 wikilink to a related note",
        "Has 0 broken wikilinks",
        "Contains reasoning language (because, therefore, which means, this suggests)",
        "Is self-contained — a reader can understand it without reading other notes",
    ],
    "pass_criteria": "All checks pass with 0 broken wikilinks",
    "action": "Call vault_lint on the note. If any broken wikilinks, fix or escape them. If missing frontmatter, add it. If no reasoning language, rewrite the synthesis.",
})
```

[validate: contains "lint_required"]

## Decision Points

- If research returned fewer than 3 sources: flag the note as low-confidence in the summary.
- If findings contradict each other: note the contradiction explicitly. Don't smooth it over.
- If the topic is a procedure (how-to): use the procedural note schema instead.

## Common Failure Modes

| Failure | Fix |
|---|---|
| Fact dump — no synthesis | Add a "What this means" paragraph with reasoning language |
| No links — isolated note | Search vault for related notes and link them |
| Missing frontmatter | Add the standard frontmatter block |
| Broken wikilinks | Run vault_lint and fix or escape broken links |
| Source not cited | Add [sources: Source Title] inline |

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] — the full plan this procedure is part of
- [[Vault-Knowledge-Only-Directive]] — vault is the only knowledge source
- [[IDK-Fallback-Directive]] — what to do when research fails
- [[Verify-Claims]] — run after this to verify claims in the note