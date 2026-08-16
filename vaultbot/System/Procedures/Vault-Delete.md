---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Safely delete a note from the vault with backup.
when_to_use: When a note is garbage, duplicate, or needs to be removed. Backs up content before deleting.
allowed_tools:
  - vault_delete
  - vault_search
summary: 1. Safely delete note from Vault while backing up content before deleting sacred files with verified checks to prevent accidental loss or unauthorized access issues; 2|vault_delete|backups_safety_chec
tags:
  - procedure
  - procedures
---

# Vault-Delete

Safely delete a note from the vault. Backs up content to vaultbot_backend/trash/ before deleting. Has safety checks to prevent deleting sacred files.

## Steps

1. [llm: Search for the note to confirm it exists and should be deleted.]

2. ```python
   result = vault_delete(note_path=args["note_path"])
   print(result)
   ```

3. [llm: Confirm the deletion succeeded. If it was blocked by a safety check, respect the block and tell the user why.]