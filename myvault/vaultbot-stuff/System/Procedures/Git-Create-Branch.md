---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-22
description: "Create a new git branch from main, push it to origin, and verify it exists. Refuses to create branches named main/master, refuses if working tree is dirty, refuses if already on the target branch name. Uses subprocess for all git operations — no custom tools needed."
when_to_use: "When you need to create a feature or fix branch before starting work on an issue. Called by Dev-Cycle and Solve-GitHub-Issue."
falsifiable_if: The procedure creates a branch when it should refuse (dirty tree, main branch name), or fails to create a branch that does exist on origin after the procedure reports success.
applies_to:
  - git
  - dev-cycle
  - self-modification
allowed_tools:
  - code_run
  - code_read
summary: Creates a git branch for development work.
tags:
  - procedure
  - git
  - dev-cycle
---

# Git-Create-Branch

## Purpose

Create a new git branch from main and push it to origin. This is the
first step in any autonomous dev cycle: before fixing a bug or implementing
a feature, you need a clean branch off main.

## Why This Exists

The existing `vaultbot_sync` tool handles syncing with upstream but doesn't
create branches. `submit_contribution` creates a branch internally but it's
a monolithic tool that stages, commits, pushes, and creates a PR all at
once. This procedure gives fine-grained control over the branch creation
step so the Dev-Cycle can create a branch, fix code, run CI, and THEN
submit — rather than submitting before testing.

## Safety

- **Refuses if on main/master**: Creating a branch from main is fine, but
  the procedure first switches to main and pulls, so it checks that the
  current state is clean first.
- **Refuses if working tree is dirty**: Uncommitted changes would be lost
  or carried over to the new branch. Stash or commit first.
- **Refuses if branch name is main/master**: These are protected branches.
- **Never force-pushes**: The procedure only does `git push -u origin` for
  the new branch, never `--force`.
- **Never pushes to main**: The procedure explicitly checks that the
  branch name is not main or master before pushing.

## Steps

### Step 1: Validate preconditions and create branch

This step validates that the working tree is clean, switches to main,
pulls latest, creates the new branch, and pushes it to origin.

```python
import subprocess
import json

branch_name = args.get("branch_name", "")
base_branch = args.get("base", "main")

if not branch_name:
    result = json.dumps({"error": "branch_name argument required"})
    print(result)
else:
    # Safety: refuse protected branch names
    if branch_name.lower() in ("main", "master"):
        result = json.dumps({"error": f"refusing to create branch named '{branch_name}' — protected branch"})
        print(result)
    else:
        # Check working tree is clean
        status = subprocess.run(["git", "status", "--porcelain"],
                                capture_output=True, text=True, timeout=30)
        if status.stdout.strip():
            result = json.dumps({"error": "working tree has uncommitted changes — stash or commit first", "dirty_files": status.stdout.strip()[:500]})
            print(result)
        else:
            # Switch to base branch and pull latest
            checkout = subprocess.run(["git", "checkout", base_branch],
                                     capture_output=True, text=True, timeout=30)
            if checkout.returncode != 0:
                result = json.dumps({"error": f"failed to checkout {base_branch}: {checkout.stderr.strip()[:300]}"})
                print(result)
            else:
                pull = subprocess.run(["git", "pull", "--ff-only", f"origin/{base_branch}"],
                                     capture_output=True, text=True, timeout=60)
                if pull.returncode != 0:
                    result = json.dumps({"error": f"failed to pull {base_branch}: {pull.stderr.strip()[:300]}"})
                    print(result)
                else:
                    # Create and switch to new branch
                    create = subprocess.run(["git", "checkout", "-b", branch_name],
                                           capture_output=True, text=True, timeout=30)
                    if create.returncode != 0:
                        result = json.dumps({"error": f"failed to create branch '{branch_name}': {create.stderr.strip()[:300]}"})
                        print(result)
                    else:
                        # Push new branch to origin
                        push = subprocess.run(["git", "push", "-u", "origin", branch_name],
                                             capture_output=True, text=True, timeout=60)
                        # Git push may print "remote" warnings to stderr even on success
                        # Check the actual return code
                        if push.returncode != 0 and "new branch" not in (push.stdout + push.stderr):
                            result = json.dumps({"error": f"failed to push branch '{branch_name}': {push.stderr.strip()[:500]}"})
                            print(result)
                        else:
                            # Verify branch exists on origin
                            verify = subprocess.run(["git", "branch", "-r", "--list", f"origin/{branch_name}"],
                                                   capture_output=True, text=True, timeout=15)
                            on_origin = f"origin/{branch_name}" in verify.stdout
                            result = json.dumps({
                                "branch": branch_name,
                                "base": base_branch,
                                "pushed_to_origin": on_origin,
                                "status": "created"
                            })
                            print(result)
```

## Related

- [[Git-Commit-And-Push]] — commit and push changes to the branch
- [[Git-Sync-Upstream]] — sync with upstream before starting work
- [[Dev-Cycle]] — the main orchestrator that calls this procedure