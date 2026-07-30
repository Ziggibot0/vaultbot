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
        "Requires GITHUB_TOKEN in .env with repo scope."
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
    import json
    import subprocess
    import requests

    # Add backend to path for subprocess_utils
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from subprocess_utils import run as _subprocess_run

    def run_git(git_args, cwd):
        try:
            r = _subprocess_run(["git"] + git_args, capture_output=True, text=True, timeout=60, cwd=cwd)
            return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
        except Exception as e:
            return False, "", str(e)

    title = args.get("title", "").strip()
    if not title:
        return {"error": "title is required"}

    description = args.get("description", "").strip()
    specific_files = args.get("files", [])

    # Find the vault root (2 levels up from vaultbot_backend/ -> vaultbot_stuff/ -> vault root)
    vault_root = os.path.dirname(os.path.dirname(backend_dir))

    # 1. Check for GITHUB_TOKEN
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return {
            "error": "GITHUB_TOKEN not found in environment.",
            "hint": (
                "Create a GitHub personal access token with 'repo' scope at "
                "https://github.com/settings/tokens and add it to your .env file "
                "as GITHUB_TOKEN=ghp_your_token_here"
            ),
        }

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 2. Get remote URL and parse owner/repo
    ok, remote_url, err = run_git(["remote", "get-url", "origin"], vault_root)
    if not ok:
        return {"error": f"Could not get git remote URL: {err}"}

    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if not match:
        return {"error": f"Could not parse GitHub owner/repo from remote URL: {remote_url}"}
    upstream_owner, upstream_repo = match.group(1), match.group(2)

    # 3. Get the authenticated user's GitHub username
    try:
        resp = requests.get("https://api.github.com/user", headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"error": f"Could not get GitHub user info: {resp.status_code}"}
        gh_username = resp.json().get("login", "")
        if not gh_username:
            return {"error": "Could not determine GitHub username from token"}
    except Exception as e:
        return {"error": f"Failed to get GitHub user info: {e}"}

    # 4. Check if user has push access to the upstream repo
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}",
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return {"error": f"Could not access upstream repo: {resp.status_code}"}
        permissions = resp.json().get("permissions", {})
        has_push_access = permissions.get("push", False)
    except Exception as e:
        return {"error": f"Failed to check repo permissions: {e}"}

    # 5. Safety scan — never commit sensitive files
    ok, staged_preview, _ = run_git(["add", "-A", "--dry-run"], vault_root)
    danger_patterns = ["'.env'", "'vaultbot_venv/", "sessions/", "vaultbot_index/",
                        "data.json'", "mcp.json'", "workspace.json"]
    for pat in danger_patterns:
        if pat in staged_preview:
            for line in staged_preview.split('\n'):
                if pat.strip("'") in line and not line.startswith("remove"):
                    return {
                        "error": f"Refusing to commit: sensitive file would be staged: {pat}",
                        "hint": "Check your .gitignore. This file should not be committed.",
                    }

    # 6. Stage changes
    if specific_files:
        for f in specific_files:
            ok, _, err = run_git(["add", f], vault_root)
            if not ok:
                return {"error": f"Could not stage file {f}: {err}"}
    else:
        ok, _, err = run_git(["add", "-A"], vault_root)
        if not ok:
            return {"error": f"Could not stage changes: {err}"}

    ok, diff_stat, _ = run_git(["diff", "--cached", "--stat"], vault_root)
    if not diff_stat.strip():
        return {"error": "No staged changes to submit. Make your changes first, then run this tool."}

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
        ok, push_out, push_err = run_git(["push", "-u", "origin", branch_name], vault_root)
        if not ok:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {
                "error": f"Could not push branch to origin: {push_err}",
                "hint": "Make sure your GITHUB_TOKEN has push access to the repository.",
            }

        pr_head = branch_name
    else:
        # === FORK-BASED FLOW (user does NOT have write access) ===
        # 9a. Fork the upstream repo
        try:
            resp = requests.post(
                f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}/forks",
                headers=headers,
                timeout=30,
            )
            if resp.status_code not in (200, 202):
                run_git(["checkout", "main"], vault_root)
                run_git(["branch", "-D", branch_name], vault_root)
                return {"error": f"Could not fork repo: {resp.status_code} {resp.text[:200]}"}
        except Exception as e:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {"error": f"Failed to fork repo: {e}"}

        # 9b. Wait for fork to be ready
        if resp.status_code == 202:
            time.sleep(5)

        # 9c. Add fork as a remote (or update if exists)
        fork_url = f"https://github.com/{gh_username}/{upstream_repo}.git"
        ok, _, _ = run_git(["remote", "get-url", "fork"], vault_root)
        if ok:
            run_git(["remote", "set-url", "fork", fork_url], vault_root)
        else:
            run_git(["remote", "add", "fork", fork_url], vault_root)

        # 9d. Push to fork
        ok, push_out, push_err = run_git(["push", "-u", "fork", branch_name], vault_root)
        if not ok:
            time.sleep(10)
            ok, push_out, push_err = run_git(["push", "-u", "fork", branch_name], vault_root)
        if not ok:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {
                "error": f"Could not push to fork: {push_err}",
                "hint": f"Make sure your fork exists at {fork_url} and your token has push access to it.",
            }

        pr_head = f"{gh_username}:{branch_name}"

    # 10. Create PR via GitHub API
    pr_body = f"""## Community Contribution

{description or 'No description provided.'}

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
        resp = requests.post(
            f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}/pulls",
            headers=headers,
            json=pr_payload,
            timeout=30,
        )
        if resp.status_code == 201:
            pr_data = resp.json()
            pr_url = pr_data.get("html_url", "")
            run_git(["checkout", "main"], vault_root)
            return {
                "status": "success",
                "pr_url": pr_url,
                "branch": branch_name,
                "flow": "direct" if has_push_access else "fork",
                "message": f"PR created successfully: {pr_url}",
            }
        else:
            error_msg = resp.json().get("message", str(resp.status_code))
            run_git(["checkout", "main"], vault_root)
            return {
                "error": f"GitHub API returned {resp.status_code}: {error_msg}",
                "hint": (
                    "The branch was pushed but the PR could not be created. "
                    f"Create it manually at "
                    f"https://github.com/{upstream_owner}/{upstream_repo}/compare/main...{pr_head.replace(':', '-')}"
                ),
            }
    except Exception as e:
        run_git(["checkout", "main"], vault_root)
        return {
            "error": f"Failed to create PR: {e}",
            "hint": (
                "The branch was pushed but the PR could not be created. "
                f"Create it manually at "
                f"https://github.com/{upstream_owner}/{upstream_repo}/compare/main...{pr_head.replace(':', '-')}"
            ),
        }