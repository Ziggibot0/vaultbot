"""
Agent-authored tool: submit_contribution
"""

SCHEMA = {"name": "submit_contribution", "description": "Submit uncommitted changes as a GitHub pull request for community review. Creates a branch, commits changes, pushes to origin, and opens a PR. Requires GITHUB_TOKEN in .env with repo scope. The PR targets the main branch and includes the diff description.", "parameters": {"properties": {"description": {"description": "Description of what the changes do and why. Will be used as the PR body.", "type": "string"}, "files": {"description": "Optional list of specific files to include. If omitted, all uncommitted changes are included.", "items": {"type": "string"}, "type": "array"}, "title": {"description": "Short title for the pull request (e.g. 'Fix subprocess window popup on Windows')", "type": "string"}}, "required": ["title"], "type": "object"}}

def run(args: dict) -> dict:
    """Submit uncommitted changes as a GitHub pull request.

    Flow:
    1. Check for GITHUB_TOKEN in environment
    2. Get uncommitted changes (git diff)
    3. Create a contribution branch
    4. Commit the changes
    5. Push to origin
    6. Create a PR via the GitHub API

    Returns a dict with the PR URL on success, or an error message.
    """
    import os
    import re
    import sys
    import time
    import json
    import requests

    # Add backend to path for subprocess_utils
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # custom_tools/ -> vaultbot_backend/
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
            "hint": "Create a GitHub personal access token with 'repo' scope at "
                    "https://github.com/settings/tokens and add it to your .env file "
                    "as GITHUB_TOKEN=ghp_your_token_here"
        }

    # 2. Get remote URL and parse owner/repo
    ok, remote_url, err = run_git(["remote", "get-url", "origin"], vault_root)
    if not ok:
        return {"error": f"Could not get git remote URL: {err}"}

    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if not match:
        return {"error": f"Could not parse GitHub owner/repo from remote URL: {remote_url}"}
    owner, repo = match.group(1), match.group(2)

    # 3. Check for uncommitted changes
    if specific_files:
        # Stage only specific files
        for f in specific_files:
            ok, _, err = run_git(["add", f], vault_root)
            if not ok:
                return {"error": f"Could not stage file {f}: {err}"}
    else:
        # Stage all uncommitted changes
        ok, _, err = run_git(["add", "-A"], vault_root)
        if not ok:
            return {"error": f"Could not stage changes: {err}"}

    ok, diff_stat, _ = run_git(["diff", "--cached", "--stat"], vault_root)
    if not diff_stat.strip():
        return {"error": "No staged changes to submit. Make your changes first, then run this tool."}

    # 4. Create a contribution branch
    branch_name = f"contribution-{int(time.time())}"
    ok, _, err = run_git(["checkout", "-b", branch_name], vault_root)
    if not ok:
        return {"error": f"Could not create branch {branch_name}: {err}"}

    # 5. Commit the changes
    commit_msg = f"{title}\n\n{description}" if description else title
    ok, _, err = run_git(["commit", "-m", commit_msg], vault_root)
    if not ok:
        # Try to go back to main on failure
        run_git(["checkout", "main"], vault_root)
        return {"error": f"Could not commit changes: {err}"}

    # 6. Push to origin
    ok, push_out, push_err = run_git(["push", "-u", "origin", branch_name], vault_root)
    if not ok:
        # Try to go back to main on failure
        run_git(["checkout", "main"], vault_root)
        run_git(["branch", "-D", branch_name], vault_root)
        return {
            "error": f"Could not push branch to origin: {push_err}",
            "hint": "Make sure your GITHUB_TOKEN has push access to the repository, "
                    "or that you have configured git credentials for pushing."
        }

    # 7. Create PR via GitHub API
    pr_body = f"""## Community Contribution

{description or 'No description provided.'}

### Changed files
```
{diff_stat}
```

---
*This PR was submitted via VaultBot's community contribution command.*
*Submitted by: @{os.environ.get("GITHUB_USERNAME", "vaultbot-user")}*
"""

    pr_payload = {
        "title": title,
        "body": pr_body,
        "head": branch_name,
        "base": "main",
    }

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers=headers,
            json=pr_payload,
            timeout=30,
        )
        if resp.status_code == 201:
            pr_data = resp.json()
            pr_url = pr_data.get("html_url", "")
            # Switch back to main
            run_git(["checkout", "main"], vault_root)
            return {
                "status": "success",
                "pr_url": pr_url,
                "branch": branch_name,
                "message": f"PR created successfully: {pr_url}"
            }
        else:
            error_msg = resp.json().get("message", str(resp.status_code))
            # Switch back to main
            run_git(["checkout", "main"], vault_root)
            return {
                "error": f"GitHub API returned {resp.status_code}: {error_msg}",
                "hint": "The branch was pushed successfully but the PR could not be created. "
                        "You can create the PR manually at "
                        f"https://github.com/{owner}/{repo}/compare/main...{branch_name}"
            }
    except Exception as e:
        # Switch back to main
        run_git(["checkout", "main"], vault_root)
        return {
            "error": f"Failed to create PR: {e}",
            "hint": "The branch was pushed successfully but the PR could not be created. "
                    "You can create the PR manually at "
                    f"https://github.com/{owner}/{repo}/compare/main...{branch_name}"
        }
