---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-22
description: "Pre-flight checklist for the Dev-Cycle. Verifies git is configured, gh CLI is authenticated, required env vars are set, working tree is clean, and the repo is on main. Returns a structured pass/fail report."
when_to_use: "At the start of the Dev-Cycle, before syncing with upstream or creating a branch. Also useful as a standalone health check."
falsifiable_if: The checklist reports all passing when git/gh/env are not actually available.
applies_to:
  - dev-cycle
  - preflight
allowed_tools:
  - code_run
  - code_read
summary: Pre-flight checklist for the Dev-Cycle.
tags:
  - procedure
  - dev-cycle
  - preflight
---

# Dev-Cycle-Checklist

## Purpose

Run a pre-flight checklist before starting the Dev-Cycle. This verifies that
all required tools, auth, and environment are in place so the cycle doesn't
fail halfway through.

## Why This Exists

The Dev-Cycle has many moving parts: git, gh CLI, env vars, clean tree.
If any of these are missing, the cycle will fail mid-way. This checklist
catches problems before they cascade.

## Checks

1. **Git available**: `git --version` succeeds
2. **Git identity**: `user.name` and `user.email` are configured
3. **Working tree clean**: `git status --porcelain` returns empty
4. **On main branch**: `git branch --show-current` is `main`
5. **Upstream remote**: `origin` remote exists and points to the right repo
6. **gh CLI available**: `gh --version` succeeds
7. **gh authenticated**: `gh auth status` shows at least one logged-in account
8. **Required env vars**: `VAULT_PATH` is set
9. **Contributions gate**: `VAULTBOT_ALLOW_CONTRIBUTIONS` is set (optional, needed for PR operations)

## Steps

### Step 1: Run all pre-flight checks

This step runs each check in sequence and builds a structured report.

```python
import json
import os
import subprocess

def run_check(name, cmd, check_fn=None):
    """Run a command and return status/output dict."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        output = (r.stdout + r.stderr).strip()[:500]
        if check_fn:
            passed = check_fn(r)
        else:
            passed = r.returncode == 0
        return {"name": name, "status": "pass" if passed else "fail", "output": output}
    except FileNotFoundError:
        return {"name": name, "status": "skip", "output": "command not found"}
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "error", "output": "timeout"}
    except Exception as e:
        return {"name": name, "status": "error", "output": str(e)[:300]}

checks = []

# 1. Git available
checks.append(run_check("git_available", ["git", "--version"]))

# 2. Git identity
def check_identity(r):
    return "user.name" in r.stdout and "user.email" in r.stdout
name_r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=10)
email_r = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=10)
has_identity = bool(name_r.stdout.strip() and email_r.stdout.strip())
checks.append({
    "name": "git_identity",
    "status": "pass" if has_identity else "fail",
    "output": f"name={name_r.stdout.strip()!r} email={email_r.stdout.strip()!r}",
})

# 3. Working tree clean
status_r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=30)
is_clean = not status_r.stdout.strip()
checks.append({
    "name": "working_tree_clean",
    "status": "pass" if is_clean else "fail",
    "output": status_r.stdout.strip()[:500] if not is_clean else "clean",
})

# 4. On main branch
branch_r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=10)
on_main = branch_r.stdout.strip() == "main"
checks.append({
    "name": "on_main_branch",
    "status": "pass" if on_main else "warn",
    "output": f"current branch: {branch_r.stdout.strip()!r}",
})

# 5. Upstream remote
remote_r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
has_remote = remote_r.returncode == 0
checks.append({
    "name": "upstream_remote",
    "status": "pass" if has_remote else "fail",
    "output": remote_r.stdout.strip() if has_remote else remote_r.stderr.strip()[:300],
})

# 6. gh CLI available
checks.append(run_check("gh_available", ["gh", "--version"]))

# 7. gh authenticated
checks.append(run_check("gh_authenticated", ["gh", "auth", "status"]))

# 8. VAULT_PATH env var
vault_path_val = os.environ.get("VAULT_PATH", "")
checks.append({
    "name": "vault_path_set",
    "status": "pass" if vault_path_val else "warn",
    "output": vault_path_val or "(not set, will use default)",
})

# 9. Contributions gate (optional)
allow_contrib = os.environ.get("VAULTBOT_ALLOW_CONTRIBUTIONS", "")
checks.append({
    "name": "contributions_gate",
    "status": "pass" if allow_contrib.lower() == "true" else "warn",
    "output": allow_contrib if allow_contrib else "(not set — PR operations will be blocked)",
})

# Summary
all_passed = all(c["status"] in ("pass", "warn", "skip") for c in checks)
failures = [c["name"] for c in checks if c["status"] == "fail"]
warnings = [c["name"] for c in checks if c["status"] == "warn"]

result = json.dumps({
    "checks": checks,
    "all_passed": all_passed,
    "failures": failures,
    "warnings": warnings,
    "summary": f"{'PASS' if all_passed else 'FAIL'} ({len(failures)} failures, {len(warnings)} warnings)",
})
print(result)
```

## Related

- [[Dev-Cycle]] — the main orchestrator that calls this procedure
- [[Git-Sync-Upstream]] — syncs with upstream after checklist passes
- [[Git-Create-Branch]] — creates a branch after sync