---
type: procedure
title: Safe Commit Push Procedure
tags:
  - procedure
  - git
  - safety
  - release
allowed_tools:
  - code_run
status: raw
baseline: true
created: 2026-08-03
summary: Safe Commit & Push Procedure Requires Strict .env isolation and Git ignore handling of VaultBot-specific paths to prevent personal data leakage. | CODE, SECURITY_CHECKOUT, VOLUME_CONTROL
description: Commit and push VaultBot code changes to GitHub without leaking personal data, API keys, or user content.
when_to_use: when the operator authorizes a release, or when VaultBot needs to commit and push code changes safely
falsifiable_if: personal data, API keys, or user content is committed, or a sensitive path is not covered by .gitignore
---

# Safe Commit & Push Procedure

## Purpose

Commit and push VaultBot code changes to GitHub **without ever leaking
personal data, API keys, or user content**. This procedure is run by
VaultBot when the operator authorizes a release.

## Pre-conditions

- `.env` exists and contains `GITHUB_TOKEN` (gitignored — never committed)
- `.gitignore` is functional and covers all sensitive paths
- Git working tree has uncommitted changes to release

## Steps

### Step 1: Verify .gitignore is working (CODE — zero LLM cost)

Run `git check-ignore` against every sensitive path. If ANY path is NOT
ignored, **STOP immediately** and report to the operator. Do not proceed.

Sensitive paths that MUST be ignored:
- `.env`
- `vaultbot_venv/`
- `User/`
- `vaultbot/Memory/`
- `vaultbot/Knowledge/`
- `vaultbot/vaultbot_backend/sessions/`
- `vaultbot/vaultbot_backend/identity/`
- `vaultbot/vaultbot_backend/vaultbot_index/`
- `vaultbot/vaultbot_backend/trash/`
- `vaultbot/vaultbot_backend/checkpoints/`
- `vaultbot/learningMaterial/`
- `.obsidian/plugins/vaultbot/data.json`
- `.obsidian/plugins/vaultbot/mcp.json`
- `.vscode/mcp.json`
- `.obsidian/workspace.json`

```python
import subprocess, os, sys

vault_root = os.getcwd()  # or pass as arg
sensitive = [
    '.env', 'vaultbot_venv/', 'User/',
    'vaultbot/Memory/', 'vaultbot/Knowledge/',
    'vaultbot/vaultbot_backend/sessions/',
    'vaultbot/vaultbot_backend/identity/',
    'vaultbot/vaultbot_backend/vaultbot_index/',
    'vaultbot/vaultbot_backend/trash/',
    'vaultbot/vaultbot_backend/checkpoints/',
    'vaultbot/learningMaterial/',
    '.obsidian/plugins/vaultbot/data.json',
    '.obsidian/plugins/vaultbot/mcp.json',
    '.vscode/mcp.json',
    '.obsidian/workspace.json',
]
all_safe = True
for p in sensitive:
    r = subprocess.run(['git', 'check-ignore', p], cwd=vault_root,
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        print(f"BLOCKED: {p} is NOT gitignored — would be committed!")
        all_safe = False
if all_safe:
    print("PASS: All sensitive paths are gitignored")
else:
    print("FAIL: Refusing to commit — fix .gitignore first")
    sys.exit(1)
```

### Step 2: Preview what would be committed (CODE — zero LLM cost)

Show the full list of files that `git add -A` would stage. Scan for any
file that looks personal (contains personal names, has a date-only filename, is in
`User/`, `Memory/`, `Knowledge/`, `sessions/`, etc.). If any suspicious file
appears, **STOP and report to the operator**.

```python
import subprocess

# See what's untracked + modified (what git add -A would stage)
r = subprocess.run(['git', 'status', '--porcelain'], cwd=vault_root,
                   capture_output=True, text=True, timeout=30)
lines = r.stdout.strip().split('\n')

suspicious_patterns = ['Sean', 'skell'  # personal name patterns, '.env', 'sessions/', 'identity/',
                       'User/', 'Memory/', 'Knowledge/', 'learningMaterial/',
                       'workspace.json', 'mcp.json', 'data.json']
flagged = []
for line in lines:
    for pat in suspicious_patterns:
        if pat in line:
            flagged.append(line)
            break

if flagged:
    print("SUSPICIOUS FILES STAGED:")
    for f in flagged:
        print(f"  {f}")
    print("\nRefusing to commit — check these files manually")
    sys.exit(1)
else:
    print(f"PASS: {len(lines)} files to commit, none suspicious")
    for line in lines[:20]:
        print(f"  {line}")
    if len(lines) > 20:
        print(f"  ... and {len(lines)-20} more")
```

### Step 3: Verify .env would not be in the commit (CODE — zero LLM cost)

Belt-and-braces check: explicitly verify that `.env` is not staged after
a dry-run add.

```python
import subprocess

# Dry run: what would git add -A stage?
r = subprocess.run(['git', 'add', '-A', '--dry-run'], cwd=vault_root,
                   capture_output=True, text=True, timeout=30)
if '.env' in r.stdout:
    print("BLOCKED: .env would be staged!")
    sys.exit(1)
if 'vaultbot_venv' in r.stdout:
    print("BLOCKED: vaultbot_venv would be staged!")
    sys.exit(1)
print("PASS: .env and vaultbot_venv not in staging")
```

### Step 4: Commit (CODE — zero LLM cost)

Stage and commit with a descriptive message. the operator must approve the commit
message before this step runs.

```python
import subprocess

commit_msg = "{{COMMIT_MESSAGE}}"  # filled in by VaultBot

# Stage all changes
subprocess.run(['git', 'add', '-A'], cwd=vault_root, check=True, timeout=30)

# Commit
subprocess.run(['git', 'commit', '-m', commit_msg], cwd=vault_root,
               check=True, timeout=30)
print("Committed successfully")
```

### Step 5: Push to origin (CODE — zero LLM cost)

Push to the main branch (or a release branch if the operator prefers).

```python
import subprocess

# Push to origin main
subprocess.run(['git', 'push', 'origin', 'main'], cwd=vault_root,
               check=True, timeout=60)
print("Pushed to origin/main successfully")
```

### Step 6: Post-push verification (CODE — zero LLM cost)

After pushing, verify that the remote does NOT contain `.env` or any
sensitive files.

```python
import subprocess

# List files in the remote that match sensitive patterns
r = subprocess.run(['git', 'ls-tree', '-r', '--name-only', 'HEAD'],
                   cwd=vault_root, capture_output=True, text=True, timeout=30)
files = r.stdout.strip().split('\n')
sensitive = [f for f in files if any(x in f for x in
    ['.env', 'sessions/', 'identity/IDENTITY', 'workspace.json',
     'data.json', 'mcp.json', 'vaultbot_venv/', 'User/', 'Memory/',
     'Knowledge/', 'learningMaterial/'])]
if sensitive:
    print(f"WARNING: Sensitive files in commit: {sensitive}")
else:
    print("PASS: No sensitive files in the committed tree")
```

## Token Safety Guarantees

The `GITHUB_TOKEN` is:
1. **Read from environment only** — `os.environ.get("GITHUB_TOKEN")`
2. **Never written to disk** — not logged, not saved to any file
3. **Never in git history** — `.env` is gitignored; the token is never
   in a commit message, branch name, or PR body
4. **Used only for git push** — `git push` uses git's own credential
   system, not the token directly. The token is only needed if using
   the GitHub API for PR creation.
5. **Scoped to `repo` only** — the operator should create a token with minimal
   scope at https://github.com/settings/tokens

## What the Operator Should Do

1. Create a GitHub Personal Access Token at
   https://github.com/settings/tokens with **only `repo` scope**
2. Add it to `.env` as `GITHUB_TOKEN=ghp_your_token_here`
3. Add your GitHub username as `GITHUB_USERNAME=yourusername`
4. Review the procedure above
5. Tell VaultBot to execute it when ready

## What VaultBot Does

1. Run all verification steps (1-3) automatically
2. Report results to the operator — show exactly what will be committed
3. Wait for the operator's explicit approval
4. Commit and push
5. Run post-push verification
6. Report final status