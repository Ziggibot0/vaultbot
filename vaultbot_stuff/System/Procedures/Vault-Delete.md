---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Safely delete a note from the vault with backup."
when_to_use: "When a note is garbage, duplicate, or needs to be removed. Backs up content before deleting."
allowed_tools: [vault_delete, vault_search]
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