---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-07-31
description: Run torture tests on a pull request before merging. Downloads changed files and runs syntax checks, malware scans, path whitelist checks, and .gitignore tampering checks.
when: Before merging a PR to verify it passes safety checks
allowed_tools:
  - code_read
  - code_run
  - vault_search
summary: TORTURE-TEST;|RUN_TESTS,PYTHON_SYNTH_CHECK,JSSYNTH_CHECK,GITIGNORE_TAMPERING,SANITIZE_SCAN,PAT_WHITELIST

SUMMARY
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Torture-Test

Run torture tests on a pull request before merging. Downloads changed files from the PR branch and runs: Python syntax check, JS syntax check, .gitignore tampering check, malware/exfiltration pattern scan, path whitelist check. Returns a structured pass/fail report.

## Why This Exists

Merging a PR without safety checks risked introducing syntax errors, malware, or .gitignore tampering. This procedure exists to run a battery of torture tests on a PR's changed files before merge. The key tradeoff: it downloads and scans the PR branch's files rather than trusting the local working tree, so it catches what would actually be merged.

## Steps

### Step 1: Run torture tests on the pull request

1. ```python
   # Call the torture_test tool's run() function
   from custom_tools.torture_test import run as _torture
   result = _torture({"pr_number": args.get("pr_number", 0)})
   print(result)
   ```

## Related

- [[Run-CI-Gates]] — runs the CI hard gates locally
- [[Verify-Backend-Change]] — verifies and deploys a backend change
- [[Iterate-PR]] — iterates on a pull request