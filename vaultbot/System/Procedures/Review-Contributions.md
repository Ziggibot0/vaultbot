---
type: procedure
status: verified
model_cartridge: small
baseline: true
created: 2026-07-31
description: List and review open pull requests on the VaultBot GitHub repo. Fetches diffs, runs safety scans, and returns structured reports.
when: When reviewing community contributions to the VaultBot repo
allowed_tools:
  - code_read
  - vault_search
summary: "List and review open pull requests on the VaultBot GitHub repo by fetching diffs, running safety scans for secrets/dangerous patterns, and returning structured reports.

# Review-Contributions | vault"
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Review-Contributions

List and review open pull requests on the VaultBot GitHub repo. For each PR, fetches the diff, runs a safety scan (checks for secrets, dangerous code patterns, path traversal, .gitignore tampering), and returns a structured report.

## Steps

1. ```python
   # Call the review_contributions tool's run() function
   from custom_tools.review_contributions import run as _review
   result = _review({"pr_number": args.get("pr_number", 0), "merge": args.get("merge", False)})
   print(result)
   ```