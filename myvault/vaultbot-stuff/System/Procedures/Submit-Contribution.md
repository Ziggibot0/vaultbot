---
type: procedure
status: verified
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

## Why This Exists

Submitting a PR that fails CI on a mechanical lint/format/test error wastes a review round-trip. This procedure closes that gap by enforcing the CI hard gates locally before pushing, and refusing to push if any fail. The tradeoff is that it handles both write-access and fork paths, and the `skip_ci=true` argument bypasses the gate only when the tree is already known to be CI-clean.

## Pre-flight CI gate (enforced)

Before pushing, the `submit_contribution` tool runs the CI hard gates
locally — `ruff check` (full rule set), `ruff format --check`, and
`pytest -m unit` — and **refuses to push** if any fail. This prevents
VaultBot from submitting a PR that will fail CI on a mechanical
lint/format/test error (see issue #80). If the gates fail, fix the
failures (use the [[Run-CI-Gates]] procedure to reproduce them), then
re-run. The `skip_ci=true` argument bypasses this check and should only
be used when the tree is already known to be CI-clean.

## Steps

### Step 1: Submit changes as a GitHub pull request

1. ```python
   # Call the submit_contribution tool's run() function
   from custom_tools.submit_contribution import run as _submit
   result = _submit({
       "title": args.get("title", ""),
       "description": args.get("description", ""),
       "files": args.get("files", []),
       "skip_ci": args.get("skip_ci", False),
   })
   print(result)
   ```

## Related

- [[Review-Contributions]] — the review side of the contribution flow
- [[Run-CI-Gates]] — the CI gates this procedure enforces before pushing
- [[Solve-GitHub-Issue]] — the full fix-to-merge loop that ends in a PR