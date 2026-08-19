"""
Agent-authored tool: submit_contribution

Supports two flows:
1. Direct push (user has write access to upstream) — push to origin, create PR
2. Fork-based push (user does NOT have write access) — fork upstream, push to fork, create cross-fork PR

The tool auto-detects which flow to use by checking the user's permissions
on the upstream repo via the GitHub API.
"""

SCHEMA = {
    "name": "submit_contribution",
    "description": (
        "Submit uncommitted changes as a GitHub pull request for community review. "
        "If the user has write access to the upstream repo, pushes directly and creates a PR. "
        "If not, forks the repo, pushes to the fork, and creates a cross-fork PR. "
        "Requires the gh CLI authenticated via 'gh auth login'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the pull request (e.g. 'Fix subprocess window popup on Windows')",
            },
            "description": {
                "type": "string",
                "description": "Description of what the changes do and why. Will be used as the PR body.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of specific files to include. If omitted, all uncommitted changes are included.",
            },
        },
        "required": ["title"],
    },
}


def run(args: dict) -> dict:
    """Submit uncommitted changes as a GitHub pull request.

    Auto-detects whether to use direct-push or fork-based flow by checking
    the user's permissions on the upstream repo.

    Returns a dict with the PR URL on success, or an error message.
    """
    import os
    import re
    import sys
    import time

    # Add backend to path for subprocess_utils + gh_client
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from custom_tools.gh_client import GhError, gh_api, gh_available
    from subprocess_utils import run as _subprocess_run

    def run_git(git_args, cwd):
        try:
            r = _subprocess_run(
                ["git"] + git_args, capture_output=True, text=True, timeout=60, cwd=cwd
            )
            return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            return False, "", str(e)

    title = args.get("title", "").strip()
    if not title:
        return {"error": "title is required"}

    description = args.get("description", "").strip()
    specific_files = args.get("files", [])

    # Find the vault root (2 levels up from vaultbot_backend/ -> vaultbot/ -> vault root)
    vault_root = os.path.dirname(os.path.dirname(backend_dir))

    # 1. Check for gh CLI (auth is handled by gh auth login, not a token)
    if not gh_available():
        return {
            "error": "gh CLI not found or not authenticated.",
            "hint": (
                "Install the GitHub CLI from https://cli.github.com and run "
                "'gh auth login' to sign in. VaultBot uses gh for community "
                "contributions so you never have to manage a token by hand."
            ),
        }

    # 1b. Check if contributions are allowed (opt-in)
    allow_contributions = (
        os.environ.get("VAULTBOT_ALLOW_CONTRIBUTIONS", "").strip().lower()
    )
    if allow_contributions != "true":
        return {
            "error": "Contributions are not enabled.",
            "hint": (
                "Enable 'Allow contributions' in VaultBot settings (under Community contributions), "
                "or ask your operator to enable it. This is an opt-in feature \u2014 "
                "VaultBot will never submit PRs without explicit permission."
            ),
        }

    # 2. Get remote URL and parse owner/repo
    ok, remote_url, err = run_git(["remote", "get-url", "origin"], vault_root)
    if not ok:
        return {"error": f"Could not get git remote URL: {err}"}

    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if not match:
        return {
            "error": f"Could not parse GitHub owner/repo from remote URL: {remote_url}"
        }
    upstream_owner, upstream_repo = match.group(1), match.group(2)

    # 3. Get the authenticated user's GitHub username
    try:
        user_data = gh_api("GET", "user")
        gh_username = user_data.get("login", "")
        if not gh_username:
            return {"error": "Could not determine GitHub username from gh"}
    except GhError as e:
        return {"error": f"Failed to get GitHub user info: {e}"}

    # 4. Check if user has push access to the upstream repo
    try:
        repo_data = gh_api("GET", f"repos/{upstream_owner}/{upstream_repo}")
        permissions = repo_data.get("permissions", {})
        has_push_access = permissions.get("push", False)
    except GhError as e:
        return {"error": f"Failed to check repo permissions: {e}"}

    # 5. Safety scan — never commit sensitive files
    ok, staged_preview, _ = run_git(["add", "-A", "--dry-run"], vault_root)
    danger_patterns = [
        "'.env'",
        "'.venv/",
        "'vaultbot_venv/",
        "sessions/",
        "vaultbot_index/",
        "data.json'",
        "mcp.json'",
        "workspace.json",
    ]
    for pat in danger_patterns:
        if pat in staged_preview:
            for line in staged_preview.split("\n"):
                if pat.strip("'") in line and not line.startswith("remove"):
                    return {
                        "error": f"Refusing to commit: sensitive file would be staged: {pat}",
                        "hint": "Check your .gitignore. This file should not be committed.",
                    }

    # 5b. Baseline-marker filter — exclude non-baseline System/ .md files.
    # Only .md files under vaultbot/System/ are checked; .py files
    # and root-level files pass through. Files without "baseline: true" in
    # their YAML frontmatter are personal/bespoke and must not ship.
    from procedure_compiler import _parse_frontmatter as _parse_fm

    # Determine which files would be committed.
    if specific_files:
        _changed = list(specific_files)
    else:
        _ok, _changed_raw, _err = run_git(["diff", "--name-only", "HEAD"], vault_root)
        # Also include untracked files.
        _ok2, _untracked, _err2 = run_git(
            ["ls-files", "--others", "--exclude-standard"], vault_root
        )
        _changed = [p.strip() for p in _changed_raw.split("\n") if p.strip()] + [
            p.strip() for p in _untracked.split("\n") if p.strip()
        ]

    _SYSTEM_PREFIX = "vaultbot/System/"
    _excluded: list[str] = []
    _filtered_files: list[str] = []
    for _fp in _changed:
        # Only check .md files under System/.
        if not (_fp.startswith(_SYSTEM_PREFIX) and _fp.endswith(".md")):
            _filtered_files.append(_fp)
            continue
        _abs = os.path.join(vault_root, _fp)
        if not os.path.isfile(_abs):
            _filtered_files.append(_fp)  # deleted file — let it through
            continue
        try:
            with open(_abs, encoding="utf-8", errors="replace") as _fh:
                _text = _fh.read()
        except Exception:
            _excluded.append(_fp)
            continue
        _fm, _fm_str, _body = _parse_fm(_text)
        _baseline = str(_fm.get("baseline", "")).strip().lower()
        if _baseline == "true":
            _filtered_files.append(_fp)
        else:
            _excluded.append(_fp)

    if _excluded:
        _excluded_list = "\n".join(f"  - {f}" for f in _excluded)
        print(
            f"[submit_contribution] Excluded {len(_excluded)} non-baseline "
            f"file(s) from the PR:\n{_excluded_list}\n"
            f"Add 'baseline: true' to the frontmatter of any file you want "
            f"to share. See CONTRIBUTING.md → Baseline markers.",
            file=sys.stderr,
        )

    if not _filtered_files:
        return {
            "error": (
                "No baseline-marked changes to submit. "
                "All changed files are either non-baseline System/ .md files "
                "or were excluded by the safety scan."
            ),
            "hint": (
                "Add 'baseline: true' to the YAML frontmatter of any "
                "vaultbot/System/ .md file you want to share. "
                "Backend .py files are always baseline and don't need a marker. "
                "See CONTRIBUTING.md → Baseline markers for the full policy."
            ),
            "excluded_files": _excluded,
        }

    # 6. Stage changes (only the baseline-filtered set)
    for f in _filtered_files:
        ok, _, err = run_git(["add", f], vault_root)
        if not ok:
            return {"error": f"Could not stage file {f}: {err}"}

    ok, diff_stat, _ = run_git(["diff", "--cached", "--stat"], vault_root)
    if not diff_stat.strip():
        return {
            "error": "No staged changes to submit. Make your changes first, then run this tool."
        }

    # 7. Create a contribution branch
    branch_name = f"contribution-{int(time.time())}"
    ok, _, err = run_git(["checkout", "-b", branch_name], vault_root)
    if not ok:
        return {"error": f"Could not create branch {branch_name}: {err}"}

    # 8. Commit the changes
    commit_msg = f"{title}\n\n{description}" if description else title
    ok, _, err = run_git(["commit", "-m", commit_msg], vault_root)
    if not ok:
        run_git(["checkout", "main"], vault_root)
        run_git(["branch", "-D", branch_name], vault_root)
        return {"error": f"Could not commit changes: {err}"}

    # 9. Push — fork-based or direct
    if has_push_access:
        # === DIRECT PUSH FLOW (user has write access) ===
        ok, push_out, push_err = run_git(
            ["push", "-u", "origin", branch_name], vault_root
        )
        if not ok:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {
                "error": f"Could not push branch to origin: {push_err}",
                "hint": "Make sure your gh auth has push access to the repository.",
            }

        pr_head = branch_name
    else:
        # === FORK-BASED FLOW (user does NOT have write access) ===
        # 9a. Fork the upstream repo
        try:
            gh_api(
                "POST",
                f"repos/{upstream_owner}/{upstream_repo}/forks",
                timeout=30,
            )
        except GhError as e:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {"error": f"Failed to fork repo: {e}"}

        # 9b. Wait for fork to be ready (fork creation is async on GitHub)
        time.sleep(5)

        # 9c. Add fork as a remote (or update if exists)
        fork_url = f"https://github.com/{gh_username}/{upstream_repo}.git"
        ok, _, _ = run_git(["remote", "get-url", "fork"], vault_root)
        if ok:
            run_git(["remote", "set-url", "fork", fork_url], vault_root)
        else:
            run_git(["remote", "add", "fork", fork_url], vault_root)

        # 9d. Push to fork
        ok, push_out, push_err = run_git(
            ["push", "-u", "fork", branch_name], vault_root
        )
        if not ok:
            time.sleep(10)
            ok, push_out, push_err = run_git(
                ["push", "-u", "fork", branch_name], vault_root
            )
        if not ok:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {
                "error": f"Could not push to fork: {push_err}",
                "hint": f"Make sure your fork exists at {fork_url} and your gh auth has push access to it.",
            }

        pr_head = f"{gh_username}:{branch_name}"

    # 10. Create PR via GitHub API
    pr_body = f"""## Community Contribution

{description or "No description provided."}

### Changed files
```
{diff_stat}
```

---
*This PR was submitted via VaultBot's community contribution system.*
*Submitted by: @{gh_username}*
"""

    pr_payload = {
        "title": title,
        "body": pr_body,
        "head": pr_head,
        "base": "main",
    }

    try:
        pr_data = gh_api(
            "POST",
            f"repos/{upstream_owner}/{upstream_repo}/pulls",
            body=pr_payload,
            timeout=30,
        )
        pr_url = pr_data.get("html_url", "")
        run_git(["checkout", "main"], vault_root)
        return {
            "status": "success",
            "pr_url": pr_url,
            "branch": branch_name,
            "flow": "direct" if has_push_access else "fork",
            "message": f"PR created successfully: {pr_url}",
        }
    except GhError as e:
        run_git(["checkout", "main"], vault_root)
        return {
            "error": f"Failed to create PR: {e}",
            "hint": (
                "The branch was pushed but the PR could not be created. "
                f"Create it manually at "
                f"https://github.com/{upstream_owner}/{upstream_repo}/compare/main...{pr_head.replace(':', '-')}"
            ),
        }
