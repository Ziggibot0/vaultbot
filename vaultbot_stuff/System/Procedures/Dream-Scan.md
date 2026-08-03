---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Scan recent journal entries (date-only filenames) for new content. Extract themes and flag empty journals. Saves themes to a temp file for downstream dream sub-procedures."
when_to_use: "As part of a Dream-Pass cycle, or independently to check what journals have new content."
applies_to:
  - vault
  - journals
  - scanning
allowed_tools:
  - code_read
falsifiable_if: "it fails to find date-only journal files or crashes on malformed filenames"
success_count: 0
failure_count: 0
success_rate: 0.0
---

# Dream-Scan

Scans the vault for date-only journal filenames, extracts theme previews, and saves them to a temp file for downstream consolidation.

## Step 1: Scan journals and extract themes

1. ```python
import json, os, re
from datetime import date, datetime

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

# --- Find all date-only filenames (sacred journals) ---
journal_files = []
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        stem = os.path.splitext(f)[0]
        if re.match(r'^\d{4}-\d{2}-\d{2}', stem):
            journal_files.append(os.path.join(root, f))

# --- Find empty journal files (0 bytes) and flag them ---
empty_journals = []
for jf in journal_files:
    try:
        if os.path.getsize(jf) == 0:
            empty_journals.append(jf)
    except:
        pass

# --- Read journal content for theme extraction ---
journal_themes = []
for jf in journal_files:
    try:
        with open(jf, encoding='utf-8') as f:
            content = f.read()
        if len(content.strip()) > 10:
            journal_themes.append({
                "file": os.path.basename(jf),
                "preview": content[:500],
            })
    except:
        pass

# --- Save themes to a temp file for downstream steps ---
themes_path = os.path.join(vault_path, "vaultbot_backend", "_dream_pass_themes.json")
with open(themes_path, 'w', encoding='utf-8') as f:
    json.dump(journal_themes, f)

result = json.dumps({
    "journals_found": len(journal_files),
    "empty_journals": len(empty_journals),
    "themes_extracted": len(journal_themes),
    "themes_file": themes_path,
})
```