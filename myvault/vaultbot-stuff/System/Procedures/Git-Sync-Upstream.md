---
type: procedure
status: experimental
baseline: false
model_cartridge: small
created: 2026-08-22
description: "Sync the local repo with upstream main, handling stashed changes gracefully. Wraps the existing vaultbot_sync tool with stash/unstash so you don't lose uncommitted work during sync."
when_to_use: "When you need to sync with upstream before starting work on a new issue. Called by Dev-Cycle at the start of each cycle."
falsifiable_if: The procedure reports success but the local branch is not up-to-date with upstream, or loses stashed changes.
applies_to:
  - git
  - dev-cycle
  - sync
allowed_tools:
  - code_run
  - code_read
summary: Syncs local repo with upstream main.
tags:
  - procedure
  - git
  - dev-cycle
---

# Git-Sync-Upstream

## Purpose

Sync the local repo with upstream main, stashing any uncommitted changes
before the sync and restoring them afterwards. This ensures the Dev-Cycle
always starts from a clean, up-to-date main branch.

## Why This Exists

`vaultbot_sync` handles fetch + merge but doesn't stash/unstash. If the
working tree has uncommitted changes (e.g., from a previous cycle's
partial work), the sync would fail. This procedure wraps `vaultbot_sync`
with stash handling so the sync always succeeds.

## Safety

- Stashes changes before sync, restores them after
- Uses `git stash pop` (not `git stash apply`) so the stash is cleaned up
- If stash pop fails (merge conflict), reports the error but doesn't lose data

## Steps

### Step 1: Stash, sync, and restore

This step stashes any uncommitted changes, syncs with upstream, and
restores the stash.

```python
import subprocess
import json

# Check if working tree is dirty
status = subprocess.run(["git", "status", "--porcelain"],
                        capture_output=True, text=True, timeout=30)
dirty = bool(status.stdout.strip())
stashed = False

if dirty:
    # Stash changes
    stash = subprocess.run(["git", "stash", "--include-untracked"],
                          capture_output=True, text=True, timeout=60)
    if stash.returncode != 0:
        result = json.dumps({"error": f"git stash failed: {stash.stderr.strip()[:300]}"})
        print(result)
    else:
        stashed = True

if not dirty or stashed:
    # Sync with upstream using vaultbot_sync
    from custom_tools.vaultbot_sync import run as _sync
    sync_result = _sync({"target": "main"})

    if isinstance(sync_result, dict) and "error" in sync_result:
        # Sync failed — restore stash if we stashed
        if stashed:
            pop = subprocess.run(["git", "stash", "pop"],
                               capture_output=True, text=True, timeout=30)
        result = json.dumps({"error": sync_result["error"], "stashed_and_restored": stashed})
        print(result)
    else:
        # Sync succeeded — restore stash if we stashed
        stash_pop_ok = True
        stash_pop_error = None
        if stashed:
            pop = subprocess.run(["git", "stash", "pop"],
                                capture_output=True, text=True, timeout=30)
            if pop.returncode != 0:
                stash_pop_ok = False
                stash_pop_error = pop.stderr.strip()[:500]

        # Report current branch and status
        branch = subprocess.run(["git", "branch", "--show-current"],
                               capture_output=True, text=True, timeout=15)
        log = subprocess.run(["git", "log", "--oneline", "-3"],
                            capture_output=True, text=True, timeout=15)

        result_dict = {
            "sync_result": sync_result if isinstance(sync_result, str) else str(sync_result),
            "current_branch": branch.stdout.strip(),
            "recent_commits": log.stdout.strip(),
            "stashed_changes_restored": stashed,
            "stash_pop_ok": stash_pop_ok,
        }
        if stash_pop_error:
            result_dict["stash_pop_error"] = stash_pop_error

        result = json.dumps(result_dict, default=str)
        print(result)
```

## Related

- [[Git-Create-Branch]] — create a branch after syncing
- [[Dev-Cycle]] — the main orchestrator that calls this procedure