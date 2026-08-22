---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-15
description: "One-time migration: split each procedure's existing when_to_use string into a trigger frontmatter list (one item per clause) and add an empty inhibitor list. No LLM calls — pure text splitting. Idempotent: skips procedures that already have a trigger field. Leaves when_to_use in place for human readability and backward compatibility."
when_to_use: "Run ONCE after the trigger/inhibitor system is deployed to seed trigger lists from existing when_to_use fields. When migrating the vault from the old when_to_use schema to the new trigger/inhibitor schema. When trigger fields are missing from procedures that have when_to_use."
falsifiable_if: "After running, any procedure with a when_to_use field still has no trigger field, or the trigger phrases don't match the when_to_use clauses, or when_to_use was deleted."
allowed_tools:
  - vault_list
  - vault_read_note
  - md_safe_replace
tags:
  - procedure
  - migration
  - trigger
  - inhibitor
  - frontmatter
summary: "Migrate-Triggers splits each procedure's when_to_use string into a trigger frontmatter list and adds an empty inhibitor list. Pure text splitting, no LLM. Idempotent — skips procedures that already have a trigger field."
---

# Migrate-Triggers

## Purpose

One-time migration from the old `when_to_use` frontmatter schema to the new `trigger`/`inhibitor` schema. The retrieval gate (`fused_retrieval.py`) and the procedure embedding surface (`vault_indexer._embedding_text_for_note`) prefer the `trigger` list over the legacy `when_to_use` string. This migration seeds `trigger` from the existing `when_to_use` clauses so the gate works immediately without waiting for feedback to accumulate.

**Why no LLM:** The split is deterministic — `when_to_use` is already a comma-separated list of "when X, when Y, or when Z" clauses. Splitting by `, when ` produces the same clauses the embedding surface was already using. No judgment needed.

**Idempotent:** If a procedure already has a `trigger` field, it's skipped. Safe to re-run.

**Preserves `when_to_use`:** The legacy field is left in place for human readability and backward compatibility (the embedding surface falls back to it when `trigger` is absent). It is NOT deleted.

## Why This Exists

The retrieval gate and embedding surface prefer the new `trigger` list over
the legacy `when_to_use` string, but existing procedures only have
`when_to_use`. This migration seeds `trigger` from the existing clauses so
the gate works immediately. The tradeoff: it is a pure text split (no LLM),
so it is deterministic and idempotent — but it leaves `when_to_use` in place
for backward compatibility rather than deleting it.

## Steps

### Step 1: Scan all procedure notes + migrate when_to_use to trigger

```python
import re

vault_root = vault_path
all_files = vault_list()

migrated = 0
skipped_has_trigger = 0
skipped_no_wtu = 0
skipped_not_procedure = 0
errors = []

for file_path in all_files:
    # Skip non-md files
    if not file_path.endswith(".md"):
        continue
    # Skip trash/backups
    if "trash" in file_path or "_backup" in file_path:
        continue

    try:
        note_result = vault_read_note(file_path, max_lines=0)
        if isinstance(note_result, dict):
            content = note_result.get("content", "")
        else:
            content = str(note_result)
    except Exception as e:
        errors.append(f"{file_path}: read failed: {e}")
        continue

    # Check if it's a procedure
    if not content.startswith("---"):
        skipped_not_procedure += 1
        continue

    fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        skipped_not_procedure += 1
        continue

    fm = fm_match.group(1)
    if "type: procedure" not in fm:
        skipped_not_procedure += 1
        continue

    # Check if trigger already exists — idempotent skip
    if re.search(r'^trigger:', fm, re.MULTILINE):
        skipped_has_trigger += 1
        continue

    # Extract when_to_use
    wtu_match = re.search(r'^when_to_use:\s*["\']?(.*?)["\']?\s*$', fm, re.MULTILINE)
    when_to_use = wtu_match.group(1) if wtu_match else ""

    if not when_to_use:
        skipped_no_wtu += 1
        continue

    # Split when_to_use into clauses: "when X, when Y, or when Z" → [X, Y, Z]
    # Same split as vault_indexer._embedding_text_for_note.
    clauses = re.split(r',\s*(?:or\s+)?when\s+', when_to_use)
    trigger_phrases = []
    for clause in clauses:
        clause = clause.strip().rstrip(",").strip('"').strip("'").strip()
        if clause:
            trigger_phrases.append(clause)

    if not trigger_phrases:
        skipped_no_wtu += 1
        continue

    # Build the trigger list block to insert after when_to_use line
    # Find the exact when_to_use line to use as the anchor
    wtu_line_match = re.search(r'^(when_to_use:\s*.*?)(\s*)$', content, re.MULTILINE)
    if not wtu_line_match:
        skipped_no_wtu += 1
        continue

    old_anchor = wtu_line_match.group(0)
    trigger_block = f"{old_anchor}\ntrigger:"
    for phrase in trigger_phrases:
        trigger_block += f'\n  - "{phrase}"'
    # Also add empty inhibitor list if it doesn't exist
    if not re.search(r'^inhibitor:', fm, re.MULTILINE):
        trigger_block += '\ninhibitor: []'

    try:
        md_safe_replace(file_path, old_anchor, trigger_block)
        migrated += 1
    except Exception as e:
        errors.append(f"{file_path}: md_safe_replace failed: {e}")

result = (
    f"MIGRATION COMPLETE\n"
    f"  Migrated: {migrated}\n"
    f"  Skipped (already has trigger): {skipped_has_trigger}\n"
    f"  Skipped (no when_to_use): {skipped_no_wtu}\n"
    f"  Skipped (not a procedure): {skipped_not_procedure}\n"
    f"  Errors: {len(errors)}\n"
)
if errors:
    result += "  Error details:\n"
    for e in errors[:20]:
        result += f"    - {e}\n"

print(result)
```

[validate: contains "MIGRATION COMPLETE"]

## Related

- [[Dream-Trigger-Inhibitor-Update]] — updates trigger/inhibitor fields on procedures
- [[Procedure-Library-Index]] — indexes the procedure library
- [[Verify-Procedure-Discoverability]] — verifies procedures are discoverable