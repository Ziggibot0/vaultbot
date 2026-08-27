---
type: procedure
title: Review PR Procedure
tags:
  - procedure
  - github
  - review
  - safety
  - community
allowed_tools:
  - code_run
created: 2025-01-20
status: raw
baseline: true
summary: Review PR Procedure
description: Review a community PR for safety and quality before merging, checking every contribution the same way each time.
when_to_use: when the user says "review PRs" or "check contributions", or when a community PR is submitted to VaultBot
falsifiable_if: a PR with unsafe or low-quality changes is merged, or a safe PR is wrongly rejected
---

# Review PR Procedure

## Purpose

When a community PR is submitted to VaultBot, the user's VaultBot follows
this procedure to review it for safety and quality before merging.
This ensures every contribution is checked the same way, every time.

## Trigger

The user says "review PRs" or "check contributions" or similar.

## Steps

### Step 1: List and Review Open PRs (LLM)

Call `review_contributions` (no merge). This lists all open PRs,
fetches their diffs, and runs the safety scanner. The tool returns
a structured report with a verdict for each PR:

- **PASS** — all safety checks passed, ready for torture test
- **CAUTION** — some medium issues, review manually
- **REVIEW_MANUAL** — high-severity issues need human review
- **REJECT** — critical issues (secrets, sensitive files, etc.)

### Step 2: Report to the user (LLM)

Summarize the review results for the user:
- How many open PRs
- For each PR: title, author, verdict, key issues (if any)
- Which PRs are ready for torture test (PASS)
- Which PRs need the user's manual review

### Step 3: Run Torture Test on PASS PRs (LLM)

For each PR that passed the safety scan, call `torture_test` with
the PR number. This runs:
- Python syntax check on all changed .py files
- JS syntax check on changed .js files
- .gitignore tampering check
- Malware/exfiltration pattern scan
- Path whitelist check

### Step 4: Report Torture Test Results (LLM)

For each PR that was torture-tested:
- Overall verdict (PASS/FAIL)
- Which tests passed and which failed
- Specific failure details (file, error, pattern matched)

### Step 5: Merge Decision (LLM)

For PRs that passed BOTH safety scan AND torture test:
- Ask the user: "PR #X passed all checks. Merge?"
- Only merge if the user explicitly says yes
- Call `review_contributions` with `merge=True` and `pr_number=X`

For PRs that failed:
- Report the failures to the user
- Do NOT merge
- If the user asks to merge anyway, warn about the risks but respect
  the user's decision (they're the owner)

### Step 6: Post-merge Verification (CODE)

After merging, verify the merge succeeded and the main branch is clean:

```python
import subprocess, os, sys

vault_root = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# Pull the latest
subprocess.run(['git', 'fetch', 'origin'], cwd=vault_root, timeout=30)
subprocess.run(['git', 'pull', 'origin', 'main'], cwd=vault_root, timeout=30)

# Verify .env is still gitignored
r = subprocess.run(['git', 'check-ignore', '.env'], cwd=vault_root,
                   capture_output=True, text=True, timeout=10)
if r.returncode != 0:
    print("WARNING: .env is no longer gitignored after merge!")
else:
    print("OK: .env still gitignored after merge")
```

## Safety Guarantees

1. **No auto-merge** — the user must explicitly approve every merge
2. **Two-layer review** — safety scan + torture test must both pass
3. **No secrets leak** — the scanner checks for tokens, keys, .env
4. **No path traversal** — only allowed paths can be modified
5. **No .gitignore tampering** — can't un-ignore sensitive files
6. **No malware** — scans for reverse shells, deserialization, exfiltration