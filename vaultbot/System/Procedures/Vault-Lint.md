---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-07-31
description: Check a note for broken wikilinks, missing frontmatter, argument quality, and other issues. Run after writing or editing a note to verify quality. Returns a detailed report.
when_to_use: after writing or editing a note, or when the user asks to check note quality
applies_to:
  - vault-maintenance
  - quality
allowed_tools:
  - vault_lint
summary: Vault-Lint
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Vault-Lint

## When to Run This

Run this after writing or editing a note to verify quality. Also run when the user asks to check a note's quality, find broken links, or audit a note.

## Why This Exists

Notes written or edited without verification could ship with broken wikilinks, missing frontmatter, or weak arguments. This procedure exists to lint a note and return a detailed quality report. The key tradeoff: it delegates to the vault_lint tool and uses the LLM only to report results actionably, not to re-derive the checks.

## Steps

### Step 1: Lint the note

1. ```python
file_path = ""  # set to the note path relative to vault root
result = vault_lint(file_path=file_path) if hasattr(vault_lint, '__call__') else vault_lint.run({"file_path": file_path})
```

### Step 2: Report results

2. [llm: Report the lint results to the user. If there are broken wikilinks, list them. If frontmatter is missing, note it. If the argument quality checks fail, explain what's missing (too short, no wikilinks, no reasoning language). Be specific and actionable — tell the user exactly what to fix.]

### Step 3: Validate

3. [validate: contains "wikilink" or contains "frontmatter" or contains "quality"]

## Related

- [[Write-Note]] — creates notes that should be linted after
- [[Find-Broken-Links]] — finds broken wikilinks across the vault
- [[Note-Quality-Score]] — scores note quality