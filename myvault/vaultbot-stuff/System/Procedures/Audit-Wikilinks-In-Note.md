---
type: procedure
status: raw
baseline: true
created: 2026-08-10
summary: ---
tags:
  - procedure
  - procedures
description: "---"
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
allowed_tools:
  - vault_read_note
---

```markdown
---
type: procedure
description: Count wikilinks in a note and classify each as broken or valid by checking the vault, then emit a JSON report with totals and broken link titles.
when_to_use: Use when the user asks to audit, validate, or count wikilinks in a specific note, or to find which links in a note point to missing notes.
falsifiable_if: The JSON report's broken_links count does not match the number of wikilink occurrences whose targets are absent from the vault, OR total_links != broken_links + valid_links.
inputs:
  - name: note_path
    type: string
    description: Path to the note file to audit for wikilinks.
allowed_tools:
  - vault_read_note
  - vault_search
  - vault_lint
  - code_read
  - llm_generate
model_cartridge: small
---

### Step 1: Read the target note
[model_cartridge: small]

Read the note at `note_path` using vault_read_note and return its raw markdown body. If the note does not exist, emit an empty report with all counts at zero.

```python
import re, json

note = vault_read_note(note_path)
if note is None:
    report = {
        "total_links": 0,
        "broken_links": 0,
        "valid_links": 0,
        "broken_link_titles": [],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    raise SystemExit(0)

body = note["body"] if isinstance(note, dict) else note
```

## Why This Exists

Wikilinks can point to notes that don't exist, leaving broken links that degrade the vault's navigability. This procedure counts a note's wikilinks and classifies each as broken or valid by checking the vault. The key tradeoff is that the core counting is deterministic — no LLM needed — so the report is cheap and exact.

## Related

- [[Find-Broken-Links]] — vault-wide broken link scan
- [[Vault-Lint]] — link and frontmatter quality check
- [[Find-One-Way-Links]] — finds links with no backlink