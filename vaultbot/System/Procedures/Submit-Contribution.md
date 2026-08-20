---
type: procedure
status: verified
model_cartridge: small
baseline: true
created: 2026-07-31
description: Submit uncommitted changes as a GitHub pull request for community review. Forks the repo if needed, pushes, and creates a cross-fork PR.
when: When submitting code changes for community review
allowed_tools:
  - code_read
  - vault_search
summary: The note instructs users to submit uncommitted changes via a GitHub pull request, specifying two conditional paths based on upstream write access.
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Submit-Contribution

Submit uncommitted changes as a GitHub pull request for community review. If the user has write access to the upstream repo, pushes directly and creates a PR. If not, forks the repo, pushes to the fork, and creates a cross-fork PR.

## Steps

### Step 1: Submit changes as a GitHub pull request

1. ```python
   # Call the submit_contribution tool's run() function
   from custom_tools.submit_contribution import run as _submit
   result = _submit({
       "title": args.get("title", ""),
       "description": args.get("description", ""),
       "files": args.get("files", []),
   })
   print(result)
   ```