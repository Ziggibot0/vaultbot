---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Condense a verbose note to a terse, dense version. Preserves all concepts, definitions, formulas, and wikilinks while dropping repetition, scaffolding, and verbose examples. Uses the small model — condensing is simple text reduction.
when_to_use: when a note has proven its value (retrieved 3+ times) but is verbose and needs de-fluffing
falsifiable_if: the condensed note loses concepts, definitions, formulas, or wikilinks that were in the original, or invents new content
applies_to:
  - note-quality
  - de-fluff
  - vault-maintenance
allowed_tools:
  - vault_search
  - llm_generate
summary: Condense-Note
tags:
  - procedure
  - procedures
last_reviewed: 2026-08-15
---

# Condense-Note

## When to Run This

Run this procedure when a note has proven its value (retrieved 3+ times for lookup) but is verbose. It condenses the note to a terse, dense version that preserves all information while dropping pedagogical scaffolding, repetition, and verbose examples. This is the lazy de-fluff pass — notes are only condensed after they prove their value.

## Steps

### Step 1: Read the note

1. ```python
note_path = args.get("note_path", "")
if not note_path:
    result = json.dumps({"error": "missing note_path"})
else:
    try:
        content = Path(note_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        content = ""
        result = json.dumps({"error": str(e)})
    # Strip frontmatter for body-level processing
    import re
    body = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL).strip()
    result = json.dumps({"note_path": note_path, "body_length": len(body), "body": body[:24000]})
```

### Step 2: Condense with the small model

2. [llm: You are condensing a knowledge-base note to a terse, dense version. The note was retrieved multiple times for lookup, so it has proven its value — but it is verbose. Preserve ALL of: every concept, definition, and key term; every formula, equation, and numerical constant; every [[wikilink]] target (keep the [[...]] syntax intact); the logical structure / section order; headings and subheadings. Remove ALL of: repetition of the same idea in different words; pedagogical scaffolding ('let's review...', 'recall that...'); verbose worked examples that repeat the same setup — keep one representative example, drop the rest; transitional paragraphs; chatty asides. Output ONLY the condensed markdown body. No preamble. Keep all [[wikilinks]]. Do NOT add new content. The note body is in the prior step output.]

### Step 3: Write the condensed note back

3. ```python
import re
condensed = output.strip()
# Strip preamble if the model ignored the rule
for pat in (r"^Here(?:'s| is) the (?:condensed|terse|shortened)[^\n]*\n+", r"^Sure[,!]?\s+here[^\n]*\n+", r"^```(?:markdown)?\n"):
    condensed = re.sub(pat, "", condensed, count=1, flags=re.IGNORECASE)
if condensed.endswith("```\n"):
    condensed = condensed[:-4]
elif condensed.endswith("```"):
    condensed = condensed[:-3]
# Safety floor: refuse if the result is too short (model collapsed it)
if len(condensed) < 1500:
    result = json.dumps({"error": "condensed too short — refusing to write", "condensed_length": len(condensed)})
else:
    # Preserve the original frontmatter
    original = Path(note_path).read_text(encoding="utf-8", errors="replace")
    fm_match = re.match(r'^(---\n.*?\n---\n)', original, re.DOTALL)
    frontmatter = fm_match.group(1) if fm_match else ""
    new_content = frontmatter + "<!-- vaultbot:condensed -->\n" + condensed
    Path(note_path).write_text(new_content, encoding="utf-8")
    result = json.dumps({"status": "condensed", "note_path": note_path, "original_chars": len(original), "condensed_chars": len(condensed)})
```