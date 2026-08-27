---
type: procedure
status: verified
baseline: true
created: 2026-07-31
description: List all markdown notes in the vault. Optionally filter by directory or tag. Returns filenames relative to vault root. Use when you need to know what notes exist — complements semantic search.
when_to_use: when you need to see what notes are in the vault, or filter by directory/tag
applies_to:
  - vault-maintenance
  - discovery
allowed_tools:
  - vault_list
summary: Vault-List
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Vault-List

## When to Run This

Run this procedure when you need to know what notes exist in the vault — either all of them, filtered by directory, or filtered by tag. This complements semantic search (which finds by meaning) with structural listing (which finds by location).

## Why This Exists

Semantic search finds notes by meaning but can't answer "what notes exist" or "what's in this directory." This procedure exists to list all markdown notes, optionally filtered by directory or tag. The key tradeoff: it's structural listing by location, deliberately complementing rather than replacing semantic search.

## Steps

### Step 1: List vault notes

1. ```python
import os
from pathlib import Path

vault_path = os.environ.get("VAULT_PATH", ".")
directory = ""  # optional: set to filter by subdirectory
tag = ""       # optional: set to filter by tag

# The vault_list tool is injected — call it
result = vault_list(directory=directory, tag=tag) if hasattr(vault_list, '__call__') else vault_list.run({"directory": directory, "tag": tag})
```

### Step 2: Summarize results

2. [llm: Summarize the results. If the user asked for a specific directory or tag, report what was found. If the list is long, report the count and the first 20 files.]

### Step 3: Validate

3. [validate: contains "count"]

## Related

- [[Vault-Inventory]] — exact-string search across notes
- [[Vault-Walk]] — walks every note and returns per-note data
- [[Vault-Statistics]] — computes vault statistics