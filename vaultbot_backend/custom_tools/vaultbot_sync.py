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
  4. **Landing on the release (shallow-safe)** — installs are shallow,
     detached ``git clone --depth 1 --branch <tag>`` clones that share no
     history with the fetched tag, so a ``git merge`` aborts on "unrelated
     histories". Instead it fetches the target tag and ``git reset --hard``s
     onto it: no common history needed, never conflicts, idempotent.
     Untracked files (vault notes, personal data, bot-authored procedures)
     are left untouched by ``reset --hard``; tracked files with local edits
     are backed up to ``.vaultbot-update-backup/`` first.
  5. **Reporting what changed** — returns the list of files that changed
     (a tree diff that works across the shallow boundary), so the agent (or
     user) can see what the update brought.

This tool is **NOT** gated behind ``VAULTBOT_ALLOW_CONTRIBUTIONS``. Syncing
is useful for every user — even someone who never contributes still wants
updates. It requires only ``git`` on PATH (no ``gh`` auth needed for a
read-only fetch+merge).

Safety:
  - Never force-pushes and never deletes untracked files — ``reset --hard``
    only touches TRACKED files, which are curated code/content that should
    match the release.
  - Tracked files with local modifications are backed up to
    ``.vaultbot-update-backup/`` before the reset, so a bot/user edit to a
    repo-tracked file is never silently lost (recoverable, and the pre-sync
    HEAD is reported for a manual ``git reset --hard <sha>`` rollback).
  - Untracked files (vault notes, personal data, bot-authored procedures,
    keys, all gitignored runtime state) are preserved automatically.
"""

SCHEMA = {
    "name": "vaultbot_sync",
    "description": (
        "Sync VaultBot to the latest upstream release (or main). Fetches "
        "the latest release tag (or upstream/main if target='main') and "
        "lands the code exactly on it with reset --hard — shallow-clone "
        "safe, so it works on every install. Preserves all untracked files "
        "(notes, keys, bot-authored procedures); backs up tracked local "
        "edits first. This is the autonomous self-update mechanism. Does NOT "
        "require gh auth (fetch is read-only) or Allow contributions."
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


def _latest_semver_tag(ls_remote_output: str) -> str | None:
    """Pick the highest ``vMAJOR.MINOR.PATCH`` tag from ``git ls-remote
    --tags`` output. Pure + deterministic (unit-testable).

    Each ls-remote line is ``<sha>\\trefs/tags/<tag>``. We match tags of the
    release form ``vN.N.N`` and return the highest by numeric (major, minor,
    patch). Using ls-remote (not ``git describe``) is what makes tag
    resolution work on a shallow ``--depth 1`` clone, which has no local
    history for describe to walk. Returns None if no release tag is present.
    """
    import re

    best = None
    best_key = None
    for line in ls_remote_output.splitlines():
        line = line.strip()
        if not line:
            continue
        ref = line.split()[-1]
        tag = ref.rsplit("/", 1)[-1]
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if best_key is None or key > best_key:
            best_key = key
            best = tag
    return best


def _backup_modified_tracked(git_root: str) -> list[str]:
    """Back up tracked files with local modifications to
    ``.vaultbot-update-backup/<ts>/`` before a ``reset --hard``.

    Untracked files (notes, keys, bot-authored procedures) are left in place
    by ``reset --hard`` and need no backup — only tracked files whose working
    copy differs from HEAD would be discarded, so those are what we save.
    Returns the list of backed-up relative paths. Best-effort: never raises.
    """
    import os
    import shutil
    import time

    ok, out, _ = _run_git(["diff", "--name-only", "HEAD"], git_root)
    if not ok:
        return []
    modified = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not modified:
        return []
    ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
    backup_dir = os.path.join(
        git_root, "vaultbot_backend", ".vaultbot-update-backup", ts
    )
    done: list[str] = []
    for rel in modified:
        src = os.path.join(git_root, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(backup_dir, rel.replace("/", "__").replace("\\", "__"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            done.append(rel)
        except OSError:
            continue
    return done


def run(args: dict) -> dict:
    """Sync VaultBot to the latest upstream release or main branch.

    Returns a dict with the sync result: target ref, files changed, backed-up
    files, and any errors. Lands on the release with ``reset --hard`` so it
    works on the shallow, detached clones every install actually is.
    """
    import os

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

    # 2. Resolve the remote to sync from (prefer upstream, fall back to
    #    origin — a plain clone sets origin to the canonical repo).
    ok, remotes, _ = _run_git(["remote"], git_root)
    if not ok:
        return {"error": f"git remote failed: {remotes}"}
    remote_list = remotes.split()
    remote = (
        "upstream"
        if "upstream" in remote_list
        else ("origin" if "origin" in remote_list else None)
    )
    if remote is None:
        return {
            "error": (
                "No 'upstream' or 'origin' remote found. VaultBot needs a "
                "remote pointing to the canonical repo to sync."
            ),
            "hint": (
                "Add it with: git remote add upstream "
                "https://github.com/Ziggibot0/vaultbot.git"
            ),
        }

    # 3. Best-effort current branch. Empty on a detached-HEAD tag checkout,
    #    which is exactly what a `--depth 1 --branch <tag>` install is. We do
    #    NOT require it — the sync lands on the release tree directly, so a
    #    detached HEAD is fine (the old code errored out here on every real
    #    install).
    _, current_branch, _ = _run_git(["branch", "--show-current"], git_root)
    current_branch = current_branch.strip()

    # 4. Resolve the target ref and FETCH it shallowly. Installs are shallow,
    #    detached clones, so we canNOT use `git describe`/`rev-list` (they
    #    walk history a depth-1 clone doesn't have). We list the remote's tags
    #    with ls-remote (needs no local history) and pick the highest release
    #    tag, then fetch exactly that tag. `--depth 1` keeps the repo lean.
    if target == "main":
        ok, _out, err = _run_git(["fetch", "--depth", "1", remote, "main"], git_root)
        if not ok:
            return {
                "error": f"git fetch {remote} main failed: {err}",
                "hint": "Check your network connection and the remote URL.",
            }
        merge_ref = f"{remote}/main"
        target_desc = "upstream/main (bleeding edge)"
    else:
        ok, ls_out, ls_err = _run_git(
            ["ls-remote", "--tags", "--refs", remote], git_root
        )
        if not ok:
            return {
                "error": f"git ls-remote {remote} failed: {ls_err or ls_out}",
                "hint": "Check your network connection and the remote URL.",
            }
        latest_tag = _latest_semver_tag(ls_out)
        if latest_tag:
            ok, _out, err = _run_git(
                ["fetch", "--depth", "1", remote, f"refs/tags/{latest_tag}"],
                git_root,
            )
            if not ok:
                return {
                    "error": f"git fetch tag {latest_tag} failed: {err}",
                    "hint": "Check your network connection and the remote URL.",
                }
            merge_ref = latest_tag
            target_desc = f"release tag {latest_tag} (stable)"
        else:
            # No release tags on the remote — fall back to main.
            ok, _out, err = _run_git(
                ["fetch", "--depth", "1", remote, "main"], git_root
            )
            if not ok:
                return {
                    "error": f"git fetch {remote} main failed: {err}",
                    "hint": "Check your network connection and the remote URL.",
                }
            merge_ref = f"{remote}/main"
            target_desc = "upstream/main (no tags found, falling back)"

    # 5. Record the pre-update HEAD and compare it against the fetched target.
    ok, pre_head, _ = _run_git(["rev-parse", "HEAD"], git_root)
    pre_head = pre_head.strip() if ok else ""
    ok, target_sha, _ = _run_git(["rev-parse", "FETCH_HEAD"], git_root)
    target_sha = target_sha.strip() if ok else ""

    if pre_head and target_sha and pre_head == target_sha:
        return {
            "status": "already_up_to_date",
            "target": target_desc,
            "merge_ref": merge_ref,
            "current_branch": current_branch,
            "message": f"Already up to date with {target_desc}.",
        }

    # 6. Back up any TRACKED file with local modifications before we reset.
    backed_up = _backup_modified_tracked(git_root)

    # 7. Land the working tree EXACTLY on the target. No merge — a shallow,
    #    detached clone shares no history with the fetched tag, so a merge
    #    would abort on "unrelated histories". reset --hard needs only the
    #    target tree, so it can never conflict and is idempotent. Untracked
    #    files (user data, bot procedures) are preserved automatically.
    ok, _out, reset_err = _run_git(["reset", "--hard", "FETCH_HEAD"], git_root)
    if not ok:
        return {
            "error": f"git reset --hard failed: {reset_err}",
            "merge_ref": merge_ref,
            "hint": "The local repo was not changed. Check git state manually.",
        }

    # 8. Report what changed. `git diff` compares trees directly (no shared
    #    history needed), so it works even across the shallow boundary.
    ok, post_head, _ = _run_git(["rev-parse", "HEAD"], git_root)
    post_head = post_head.strip() if ok else ""

    ok, diff_out, _ = _run_git(["diff", "--stat", pre_head, post_head], git_root)
    files_changed_summary = diff_out.strip() if ok else ""

    # New commits are best-effort: a log between two disconnected shallow
    # commits may be empty, which is fine — the tree diff above is the source
    # of truth for what changed.
    ok, log_out, _ = _run_git(
        ["log", "--oneline", f"{pre_head}..{post_head}"], git_root
    )
    new_commits = (
        [line for line in log_out.splitlines() if line.strip()]
        if ok and log_out.strip()
        else []
    )

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
        "backed_up_files": backed_up,
        "message": (
            f"Synced to {target_desc} via reset --hard (shallow-safe). "
            + (
                f"Backed up {len(backed_up)} locally-modified tracked file(s)."
                if backed_up
                else "No local tracked edits needed backup."
            )
        ),
    }
