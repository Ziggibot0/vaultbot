---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-31
description: "Detect the vault's knowledge gaps: dangling wikilinks (concepts linked but no note exists) and thin notes (exist but too short). Use when the user asks what's missing or to decide what to research."
when_to_use: "when the user asks about gaps, what's missing, or what to research next"
applies_to:
  - vault-maintenance
  - research
  - curriculum
allowed_tools:
  - vault_gaps
---

# Vault-Gaps

## When to Run This

Run this when the user asks what the vault is missing, what gaps exist, or what should be researched next. Also useful before starting a research session to see where the vault is thin.

## Steps

### Step 1: Detect gaps

1. ```python
result = vault_gaps() if hasattr(vault_gaps, '__call__') else vault_gaps.run({})
```

2. [llm: Report the gaps to the user. Group them by type (dangling links, thin notes, missing entities). For each gap, explain what it means and suggest whether it's worth researching. Prioritize gaps that are referenced by many other notes.]

### Step 2: Validate

2. [validate: contains "gap"]