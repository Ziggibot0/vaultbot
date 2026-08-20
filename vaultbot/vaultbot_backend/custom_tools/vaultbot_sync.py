"""Agent-authored tool: vaultbot_sync

The missing update mechanism for VaultBot. Before this tool, a user had to
open a terminal, navigate to the right directory, and run
``git pull upstream main`` — not grandpa-proof, and the agent couldn't do it
autonomously either.

This tool is the equivalent of Hermes's ``hermes update``: one call that
fetches the latest from upstream and brings the local vault up to date. It
handles:

  1. **Finding the git root** — walks up from ``vaultbot_backend/`` to the
     nearest ``.git`` directory (works whether the repo root IS the vault
     or one level above it, e.g. ``vaultbot-fork/`` containing ``vaultbot/``).
  2. **Fetching upstream** — ``git fetch upstream --tags`` to get the latest
     commits AND release tags.
  3. **Choosing what to sync to**:
     - If release tags exist, defaults to the **latest tag** (stable). This
       is the grandpa-safe default — a tagged release has passed CI.
     - If ``target="main"`` is passed explicitly, syncs to the tip of
       ``upstream/main`` (bleeding edge, for power users).
     - If no tags exist at all, falls back to ``upstream/main``.
  4. **Merging cleanly** — uses ``git merge`` (not reset/checkout) so local
     vault notes and personal data are preserved. If there are merge
     conflicts, it reports them rather than silently overwriting.
  5. **Reporting what changed** — returns the list of files that changed
     and a summary of new commits, so the agent (or user) can see what
     the update brought.

This tool is **NOT** gated behind ``VAULTBOT_ALLOW_CONTRIBUTIONS``. Syncing
is useful for every user — even someone who never contributes still wants
updates. It requires only ``git`` on PATH (no ``gh`` auth needed for a
read-only fetch+merge).

Safety:
  - Read-only fetch + merge. Never force-pushes, never resets, never deletes
    untracked files.
  - If the working tree has uncommitted changes, it refuses and tells the
    user to commit or stash first (prevents losing work).
  - If a merge conflict occurs, it reports the conflicted files and leaves
    the merge in progress for manual resolution — it does NOT abort
    automatically (that would lose the merge state).
"""

SCHEMA = {
    "name": "vaultbot_sync",
    "description": (
        "Sync VaultBot to the latest upstream release (or main). Fetches "
        "from the upstream remote, merges the latest tagged release (or "
        "upstream/main if no tags exist or target='main' is passed), and "
        "reports what changed. This is the update mechanism — equivalent to "
        "'hermes update' for Hermes Agent. Does NOT require gh auth (fetch "
        "is read-only). Does NOT require Allow contributions to be enabled."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["latest-tag", "main"],
                "description": (
                    "What to sync to. 'latest-tag' (default) syncs to the "
                    "most recent release tag — stable, CI-verified. 'main' "
                    "syncs to the tip of upstream/main — bleeding edge."
                ),
            },
        },
    },
}


def _find_git_root(start_dir: str) -> str | None:
    """Walk up from start_dir to find the nearest .git directory."""
    import os

    current = os.path.abspath(start_dir)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None  # reached filesystem root
        current = parent


def _run_git(git_args: list[str], cwd: str) -> tuple[bool, str, str]:
    """Run a git command, return (success, stdout, stderr)."""
    import subprocess

    try:
        from subprocess_utils import run as _subprocess_run

        r = _subprocess_run(
            ["git", *git_args],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.SubprocessError) as e:
        # FileNotFoundError (git not on PATH), PermissionError, or a
        # subprocess timeout/error. The error string is returned to the
        # caller — this is not a silent swallow.
        return False, "", str(e)


def run(args: dict) -> dict:
    """Sync VaultBot to the latest upstream release or main branch.

    Returns a dict with the sync result: target ref, commits pulled, files
    changed, and any errors.
    """
    import os
    import sys

    # Add backend to path for subprocess_utils.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    target = args.get("target", "latest-tag").strip()

    # 1. Find the git root (repo root, not vault root).
    git_root = _find_git_root(backend_dir)
    if not git_root:
        return {
            "error": (
                "Could not find a .git directory by walking up from "
                f"{backend_dir}. Is VaultBot installed as a git repo?"
            ),
            "hint": (
                "Reinstall using the setup script (setup.sh / setup.ps1) "
                "which clones the repo as a git fork."
            ),
        }

    # 2. Check for uncommitted changes — refuse to merge on a dirty tree.
    ok, status_out, _ = _run_git(["status", "--porcelain"], git_root)
    if not ok:
        return {"error": f"git status failed: {status_out}"}
    if status_out.strip():
        return {
            "error": "Working tree has uncommitted changes. Refusing to merge.",
            "hint": (
                "Commit or stash your changes first, then sync. "
                "Run 'git status' to see what's changed."
            ),
            "dirty_files": status_out.strip().splitlines(),
        }

    # 3. Check that the upstream remote exists.
    ok, remotes, _ = _run_git(["remote"], git_root)
    if not ok:
        return {"error": f"git remote failed: {remotes}"}
    if "upstream" not in remotes.split():
        return {
            "error": (
                "No 'upstream' remote found. VaultBot needs an 'upstream' "
                "remote pointing to the canonical repo to sync."
            ),
            "hint": (
                "Add it with: git remote add upstream "
                "https://github.com/Ziggibot0/vaultbot.git"
            ),
        }

    # 4. Fetch upstream (with tags).
    ok, _fetch_out, fetch_err = _run_git(["fetch", "upstream", "--tags"], git_root)
    if not ok:
        return {
            "error": f"git fetch upstream failed: {fetch_err}",
            "hint": "Check your network connection and the upstream remote URL.",
        }

    # 5. Determine what to merge.
    current_branch_ok, current_branch, _ = _run_git(
        ["branch", "--show-current"], git_root
    )
    if not current_branch_ok or not current_branch:
        return {"error": "Could not determine current branch."}

    merge_ref = ""
    target_desc = ""

    if target == "main":
        merge_ref = "upstream/main"
        target_desc = "upstream/main (bleeding edge)"
    else:
        # Find the latest tag on upstream. --abbrev=0 returns just the tag
        # name (e.g. "v0.1.0"), not a describe string like "v0.1.0-14-ge06ff846"
        # which would point at the tip of main (bleeding edge, not stable).
        ok, latest_tag, _ = _run_git(
            ["describe", "--tags", "--abbrev=0", "upstream/main"], git_root
        )
        if ok and latest_tag:
            merge_ref = latest_tag.strip()
            target_desc = f"release tag {merge_ref} (stable)"
        else:
            # No tags reachable from upstream/main — fall back to main.
            merge_ref = "upstream/main"
            target_desc = "upstream/main (no tags found, falling back)"

    # 6. Check if already up to date.
    ok, behind_count, _ = _run_git(
        ["rev-list", "--count", "HEAD.." + merge_ref], git_root
    )
    if ok and behind_count.strip() == "0":
        return {
            "status": "already_up_to_date",
            "target": target_desc,
            "merge_ref": merge_ref,
            "current_branch": current_branch,
            "message": f"Already up to date with {target_desc}.",
        }

    # 7. Record the pre-merge HEAD for the diff.
    ok, pre_head, _ = _run_git(["rev-parse", "HEAD"], git_root)
    pre_head = pre_head.strip() if ok else ""

    # 8. Merge.
    ok, _merge_out, merge_err = _run_git(
        ["merge", merge_ref, "--no-edit", "--no-stat"], git_root
    )
    if not ok:
        # Check for merge conflicts.
        ok2, conflict_files, _ = _run_git(
            ["diff", "--name-only", "--diff-filter=U"], git_root
        )
        conflicted = (
            [f for f in conflict_files.splitlines() if f.strip()] if ok2 else []
        )
        return {
            "error": "Merge failed — conflicts detected.",
            "hint": (
                "Resolve the conflicts manually, then commit. "
                "Run 'git status' to see conflicted files. "
                "To abort the merge: git merge --abort"
            ),
            "merge_ref": merge_ref,
            "conflicted_files": conflicted,
            "stderr": merge_err,
        }

    # 9. Gather what changed.
    ok, log_out, _ = _run_git(["log", "--oneline", f"{pre_head}..HEAD"], git_root)
    new_commits = (
        [line for line in log_out.splitlines() if line.strip()]
        if ok and log_out.strip()
        else []
    )

    ok, diff_out, _ = _run_git(["diff", "--stat", pre_head, "HEAD"], git_root)
    files_changed_summary = diff_out.strip() if ok else ""

    ok, post_head, _ = _run_git(["rev-parse", "HEAD"], git_root)
    post_head = post_head.strip() if ok else ""

    return {
        "status": "success",
        "target": target_desc,
        "merge_ref": merge_ref,
        "current_branch": current_branch,
        "previous_head": pre_head,
        "new_head": post_head,
        "commits_pulled": len(new_commits),
        "new_commits": new_commits[:20],  # cap at 20 for context
        "files_changed_summary": files_changed_summary,
        "message": (f"Synced to {target_desc}. {len(new_commits)} new commit(s)."),
    }
