---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-22
description: "Back up ALL personal vault content (gitignored procedures, memories, chat logs, knowledge notes, build logs) to a private GitHub repo. Uses a separate 'personal' remote and a 'personal-backup' branch so the public repo never sees the files. The .gitignore protects the public repo; this procedure force-adds to the private repo only."
when_to_use: "When you want to back up your personal VaultBot content — procedures, memories, chat history — to a private GitHub repo. Run this after creating new procedures, after chat consolidation, or on a schedule."
falsifiable_if: The procedure pushes personal content to the public repo, or fails to push files that exist in the working tree.
applies_to:
  - backup
  - personal-vault
  - git
allowed_tools:
  - code_run
  - code_read
summary: Backs up gitignored personal content to a private GitHub repo.
tags:
  - procedure
  - backup
  - personal-vault
---

# Backup-Personal-Vault

## Purpose

Back up all personal VaultBot content to a private GitHub repo. This
includes the `baseline: false` procedures, Memory notes, Knowledge notes,
chat logs, and build logs — everything that's gitignored in the public repo.

## Why This Exists

The public repo (`Ziggibot0/vaultbot`) ships framework defaults. Your
personal content — custom procedures, memories, chat history — is gitignored
so it never appears in the public repo. But that means it's not backed up
anywhere. This procedure pushes it to a private repo you own, so you have
a full backup of your vault's mind.

## How It Works

1. Adds a `personal` remote pointing to your private GitHub repo (if not
   already configured)
2. Creates/switches to a `personal-backup` branch
3. Force-adds all gitignored personal files (they're ignored in the public
   repo, but `git add --force` tracks them in this branch)
4. Commits and pushes to the private repo
5. Switches back to the original branch

The key insight: `.gitignore` is per-repo, not per-branch. But `git add -f`
overrides it for specific files. The `personal-backup` branch tracks files
that `main` doesn't. Pushing the `personal-backup` branch to a *different
remote* (the private repo) means the public repo never sees them.

## Safety

- **Never pushes to origin (public repo)**: Only pushes to the `personal`
  remote (private repo).
- **Switches back to original branch**: Always returns you to where you
  were before the backup.
- **No data loss**: Uses `git add -f` which only stages files; it doesn't
  modify or delete anything.

## Prerequisites

1. Create a private GitHub repo (e.g., `Ziggibot0/vaultbot-personal`)
2. The procedure will add it as a `personal` remote if not already configured

## Input

- `args.personal_repo` (required): Full URL of the private repo
  (e.g., `https://github.com/Ziggibot0/vaultbot-personal.git`)
- `args.commit_message` (optional): Custom commit message
- `args.paths` (optional): List of specific paths to back up. If not
  provided, backs up all known personal content.

## Personal Content Paths

The following paths are backed up by default:
- `myvault/vaultbot-stuff/System/Procedures/` — all procedures (including
  `baseline: false` ones that aren't in the public repo)
- `myvault/vaultbot-stuff/Memory/` — chat logs and memory notes
- `myvault/vaultbot-stuff/Knowledge/` — generated knowledge notes
- `myvault/vaultbot/` — generated architecture notes (Codebase-Map, etc.)
- `myvault/.obsidian/plugins/vaultbot/` — plugin config

## Steps

### Step 1: Add personal remote, switch to backup branch, force-add, push, switch back

This single step does the full backup: ensures the remote exists, creates
the backup branch, force-adds all personal files, commits, pushes to the
private repo, and switches back to the original branch.

```python
import json
import subprocess
import os

personal_repo = args.get("personal_repo", "")
commit_message = args.get("commit_message", "backup: personal vault content")
custom_paths = args.get("paths")

if not personal_repo:
    result = json.dumps({"error": "personal_repo argument required (e.g. https://github.com/Ziggibot0/vaultbot-personal.git)"})
    print(result)
else:
    # Default paths to back up (all gitignored personal content)
    default_paths = [
        "myvault/vaultbot-stuff/System/Procedures/",
        "myvault/vaultbot-stuff/Memory/",
        "myvault/vaultbot-stuff/Knowledge/",
        "myvault/vaultbot/",
        "myvault/.obsidian/plugins/vaultbot/",
    ]
    paths_to_backup = custom_paths if custom_paths else default_paths

    def git(args_list, timeout=60):
        r = subprocess.run(["git"] + args_list,
                          capture_output=True, text=True, timeout=timeout)
        return r

    def git_check(args_list, timeout=60):
        """Run git, return (success, output)."""
        r = git(args_list, timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()

    errors = []
    steps_log = []

    # 1. Save current branch
    r = git(["branch", "--show-current"])
    original_branch = r.stdout.strip()
    steps_log.append(f"saved branch: {original_branch}")

    # 2. Ensure 'personal' remote exists
    remotes = git(["remote"]).stdout.split()
    if "personal" not in remotes:
        ok, out = git_check(["remote", "add", "personal", personal_repo])
        if ok:
            steps_log.append(f"added personal remote: {personal_repo}")
        else:
            # Remote might already exist with different URL; try set-url
            ok2, out2 = git_check(["remote", "set-url", "personal", personal_repo])
            if ok2:
                steps_log.append(f"updated personal remote URL: {personal_repo}")
            else:
                errors.append(f"failed to add personal remote: {out}")
    else:
        # Update URL in case it changed
        git(["remote", "set-url", "personal", personal_repo])
        steps_log.append(f"personal remote exists, URL updated")

    if not errors:
        # 3. Create or switch to personal-backup branch
        # Try to checkout existing branch first, create if it doesn't exist
        ok, out = git_check(["checkout", "personal-backup"])
        if not ok:
            ok, out = git_check(["checkout", "-b", "personal-backup"])
            if ok:
                steps_log.append("created personal-backup branch")
            else:
                errors.append(f"failed to create personal-backup branch: {out}")
        else:
            steps_log.append("switched to personal-backup branch")

    if not errors:
        # 4. Force-add all personal paths (override .gitignore)
        added_files = 0
        for path in paths_to_backup:
            if os.path.exists(path):
                ok, out = git_check(["add", "-f", path], timeout=120)
                if ok:
                    # Count files staged
                    r = git(["diff", "--cached", "--name-only"])
                    added_files = len(r.stdout.strip().splitlines()) if r.stdout.strip() else 0
                    steps_log.append(f"staged: {path}")
                else:
                    errors.append(f"failed to add {path}: {out[:200]}")
            else:
                steps_log.append(f"skipped (not found): {path}")

    if not errors:
        # 5. Commit
        ok, out = git_check(["commit", "-m", commit_message])
        if ok or "nothing to commit" in out or "no changes" in out:
            if "nothing to commit" in out or "no changes" in out:
                steps_log.append("no changes to commit (already up to date)")
            else:
                steps_log.append("committed")
        else:
            errors.append(f"commit failed: {out[:300]}")

    if not errors:
        # 6. Push to personal remote (private repo ONLY, never origin)
        ok, out = git_check(["push", "personal", "personal-backup"], timeout=120)
        # Push may show warnings but still succeed
        if ok or "personal-backup -> personal-backup" in out or "refs/heads/personal-backup" in out:
            steps_log.append("pushed to personal remote")
        else:
            # First push may need --set-upstream
            ok2, out2 = git_check(["push", "-u", "personal", "personal-backup"], timeout=120)
            if ok2 or "personal-backup -> personal-backup" in out2:
                steps_log.append("pushed to personal remote (set upstream)")
            else:
                errors.append(f"push failed: {out2[:300]}")

    # 7. ALWAYS switch back to original branch (even if errors)
    if original_branch and original_branch != "personal-backup":
        git(["checkout", original_branch])
        steps_log.append(f"switched back to {original_branch}")

    # 8. CRITICAL: Restore untracked files that git removed during branch
    # switch. When we committed untracked files to personal-backup and
    # switched back to main, git removed them from the working tree because
    # they don't exist on main. We must restore them from personal-backup
    # so the user doesn't lose their procedures/memories.
    restored_files = 0
    for path in paths_to_backup:
        # List files that were committed to personal-backup under this path
        r = git(["ls-tree", "-r", "--name-only", "personal-backup", path])
        if r.stdout.strip():
            file_list = [f for f in r.stdout.strip().splitlines() if f.strip()]
            for f in file_list:
                # Only restore if the file doesn't currently exist on main
                # (it's untracked/gitignored) — don't overwrite tracked files
                tracked_check = git(["ls-files", "--error-unmatch", f])
                if tracked_check.returncode != 0:
                    # File is not tracked on main — restore from personal-backup
                    git(["checkout", "personal-backup", "--", f])
                    # Unstage so it's untracked again (baseline:false procedures
                    # must stay untracked or the baseline marker check blocks them)
                    git(["reset", "HEAD", f])
                    restored_files += 1
    if restored_files:
        steps_log.append(f"restored {restored_files} untracked files from personal-backup")

    result = json.dumps({
        "status": "error" if errors else "ok",
        "steps": steps_log,
        "errors": errors,
        "original_branch": original_branch,
        "backup_branch": "personal-backup",
        "remote": "personal",
        "restored_files": restored_files,
    })
    print(result)
```

## Related

- [[Dev-Cycle]] — the main orchestrator (can call this after cycle completion)
- [[Git-Create-Branch]] — branch creation pattern used here