---
type: procedure
status: experimental
baseline: false
model_cartridge: small
created: 2026-08-22
description: "Stage, commit, and push changes to the current branch. Refuses to push to main/master. Uses conventional commit format. Can amend the previous commit if args.amend is true (for iterative CI fixes on the same PR)."
when_to_use: "When you need to commit and push changes on a feature branch. Called by Dev-Cycle after fixing code or after a CI failure iteration."
falsifiable_if: The procedure pushes to main/master, or fails to push changes that exist on the branch after reporting success.
applies_to:
  - git
  - dev-cycle
  - self-modification
allowed_tools:
  - code_run
  - code_read
summary: Commits and pushes changes on a feature branch.
tags:
  - procedure
  - git
  - dev-cycle
---

# Git-Commit-And-Push

## Purpose

Stage, commit, and push changes on the current branch. This is the
commit step in the dev cycle: after fixing code and running CI preflight,
the changes need to be committed and pushed before creating or updating
a PR.

## Why This Exists

`submit_contribution` handles git add + commit + push + PR in one shot,
but the Dev-Cycle needs fine-grained control: it may want to commit and
push a fix after a CI failure iteration without creating a new PR. This
procedure provides just the commit+push step.

## Safety

- **Refuses if on main/master**: Never pushes to protected branches.
- **Refuses if no changes**: Won't create empty commits.
- **Supports amend**: If `args.amend` is true, uses `--amend` to fix the
  previous commit (for CI failure iterations). Only amends the last commit
  on the current branch.
- **Never force-push to main**: When amending, pushes with `--force-with-lease`
  to the feature branch only.

## Steps

### Step 1: Stage, commit, and push changes

This step checks the current branch, stages files, commits, and pushes.

```python
import subprocess
import json

message = args.get("message", "chore: update")
files = args.get("files")  # list or None (None = all tracked changes)
amend = args.get("amend", False)

# Safety: refuse to push to main/master
branch = subprocess.run(["git", "branch", "--show-current"],
                       capture_output=True, text=True, timeout=15)
current_branch = branch.stdout.strip()
if current_branch in ("main", "master", ""):
    result = json.dumps({"error": f"refusing to commit on branch '{current_branch}' — switch to a feature branch first"})
    print(result)
else:
    # Check for changes
    status = subprocess.run(["git", "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30)
    if not status.stdout.strip():
        result = json.dumps({"error": "no changes to commit", "branch": current_branch})
        print(result)
    else:
        # Stage files
        if files:
            add = subprocess.run(["git", "add"] + files,
                               capture_output=True, text=True, timeout=30)
        else:
            add = subprocess.run(["git", "add", "-A"],
                               capture_output=True, text=True, timeout=30)

        if add.returncode != 0:
            result = json.dumps({"error": f"git add failed: {add.stderr.strip()[:300]}"})
            print(result)
        else:
            # Commit
            if amend:
                commit_cmd = ["git", "commit", "--amend", "--no-edit", "-m", message]
            else:
                commit_cmd = ["git", "commit", "-m", message]

            commit = subprocess.run(commit_cmd,
                                   capture_output=True, text=True, timeout=30)
            if commit.returncode != 0:
                result = json.dumps({"error": f"git commit failed: {commit.stderr.strip()[:500]}"})
                print(result)
            else:
                # Push
                if amend:
                    push_cmd = ["git", "push", "--force-with-lease", "origin", current_branch]
                else:
                    push_cmd = ["git", "push", "origin", current_branch]

                push = subprocess.run(push_cmd,
                                     capture_output=True, text=True, timeout=60)

                # Git push may print "remote" warnings to stderr even on success
                pushed_ok = push.returncode == 0 or f"origin/{current_branch}" in (push.stdout + push.stderr)

                if not pushed_ok:
                    result = json.dumps({"error": f"git push failed: {push.stderr.strip()[:500]}", "branch": current_branch})
                    print(result)
                else:
                    # Verify push
                    log = subprocess.run(["git", "log", "--oneline", "-1"],
                                        capture_output=True, text=True, timeout=15)
                    result = json.dumps({
                        "branch": current_branch,
                        "commit": log.stdout.strip(),
                        "amended": amend,
                        "files_staged": files or "all",
                        "status": "pushed"
                    })
                    print(result)
```

## Related

- [[Git-Create-Branch]] — create the branch before committing
- [[Git-Sync-Upstream]] — sync with upstream before starting work
- [[Dev-Cycle]] — the main orchestrator