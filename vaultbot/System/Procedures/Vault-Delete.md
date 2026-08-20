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
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Vault-Delete

Safely delete a note from the vault. Backs up content to vaultbot_backend/trash/ before deleting. Has safety checks to prevent deleting sacred files.

## Why This Exists

Deleting a note risked permanent loss of content and accidental removal of sacred files. This procedure exists to back up content to trash before deleting and to run safety checks that block sacred-file deletion. The key tradeoff: it searches to confirm the note should be deleted first, and respects any safety block rather than forcing the delete.

## Steps

### Step 1: Search for the note to confirm it should be deleted

1. [llm: Search for the note to confirm it exists and should be deleted.]

### Step 2: Delete the note with backup

2. ```python
   result = vault_delete(note_path=args["note_path"])
   print(result)
   ```

### Step 3: Confirm the deletion succeeded

3. [llm: Confirm the deletion succeeded. If it was blocked by a safety check, respect the block and tell the user why.]

## Related

- [[Vault-Cleanup]] — the meta cleanup audit that identifies deletion candidates
- [[Find-Duplicates]] — finds duplicate notes to merge or delete
- [[Safe-Write]] — the safe-write primitive for vault mutations