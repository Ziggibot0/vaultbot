---
type: procedure
status: verified
created: 2026-07-31
description: "Download or read a textbook/reference resource and ingest it into the vault as linked notes. Accepts URLs or local file paths. Supports HTML, PDF, plain text, and Markdown."
when: "When learning a new subject systematically from a textbook or reference"
allowed_tools: [vault_search, vault_list]
---

# Textbook-Ingest

Download or read a textbook/reference resource and ingest it into the vault as linked notes. Each section becomes a linked vault note with navigation. This is how VaultBot learns systematically.

## Steps

1. ```python
   # Call the textbook_ingest tool's run() function
   from custom_tools.textbook_ingest import run as _ingest
   result = _ingest({
       "source": args.get("source", ""),
       "subject": args.get("subject", ""),
       "title": args.get("title", ""),
       "max_sections": args.get("max_sections", 50),
   })
   print(result)
   ```