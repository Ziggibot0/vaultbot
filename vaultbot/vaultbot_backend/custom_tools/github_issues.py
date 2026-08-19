"""
Agent-authored tool: github_issues

Read and act on GitHub issues for the VaultBot repo. This is the missing
half of the community-contribution subsystem: review_contributions handles
PRs, but VaultBot could not see or act on *issues* — which is what it needs
to "solve its own GitHub issues."

All actions are thin wrappers over the shared ``gh_client.gh_api`` helper,
so auth is handled by ``gh auth login`` (no token management) exactly like
the other contribution tools.

SAFE-MODE GATING (content-aware, mirrors edit_lines):
  - ``list`` / ``read`` are READ-ONLY and allowed in Safe Mode.
  - ``comment`` / ``close`` / ``label`` MUTATE the repo and are blocked in
    Safe Mode (they require Developer Mode). This keeps a non-technical
    user's VaultBot from mutating GitHub while still letting it *see* issues.
"""

SCHEMA = {
    "name": "github_issues",
    "description": (
        "Read and act on GitHub issues for the VaultBot repo. Actions: "
        "'list' (open issues), 'read' (issue body + comments), 'comment' "
        "(post a comment), 'close' (close an issue with a comment), "
        "'label' (add/remove labels). 'list' and 'read' are read-only and "
        "always allowed; 'comment', 'close', and 'label' mutate the repo "
        "and require Developer Mode. Requires the gh CLI authenticated via "
        "'gh auth login'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "comment", "close", "label"],
                "description": "Which action to perform.",
            },
            "issue_number": {
                "type": "integer",
                "description": "Issue number. Required for read/comment/close/label.",
            },
            "body": {
                "type": "string",
                "description": "Comment text (for 'comment') or closing note (for 'close').",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels to add (for 'label').",
            },
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Filter for 'list'. Default: open.",
            },
        },
        "required": ["action"],
    },
}


# Actions that mutate the repo and are blocked in Safe Mode.
_MUTATING_ACTIONS = frozenset({"comment", "close", "label"})


def run(args: dict) -> dict:
    """Read or act on GitHub issues via the gh CLI.

    Returns a dict with the action result, or an error message.
    """
    import os
    import re
    import sys

    # Add backend to path for gh_client.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from custom_tools.gh_client import gh_api, gh_available, GhError

    action = args.get("action", "").strip()
    if not action:
        return {"error": "action is required (list/read/comment/close/label)"}

    issue_number = args.get("issue_number")
    body = args.get("body", "").strip()
    labels = args.get("labels", [])
    state = args.get("state", "open")

    # 1. gh CLI must be available + authenticated.
    if not gh_available():
        return {
            "error": "gh CLI not found or not authenticated.",
            "hint": (
                "Install the GitHub CLI from https://cli.github.com and run "
                "'gh auth login' to sign in."
            ),
        }

    # 2. Content-aware Safe Mode gate: block mutating actions.
    if action in _MUTATING_ACTIONS:
        try:
            from safe_mode import is_safe_mode

            if is_safe_mode():
                return {
                    "error": (
                        f"Action '{action}' is blocked in Safe Mode — it "
                        f"mutates the GitHub repo. Switch to Developer Mode "
                        f"in VaultBot Settings → Safety → uncheck 'Safe Mode' "
                        f"to enable it."
                    ),
                    "safe_mode_blocked": True,
                }
        except ImportError:
            pass  # safe_mode not available — don't block (shouldn't happen)

    # 3. Determine upstream repo (owner/repo) from git remote, with a
    #    sensible default fallback.
    upstream_owner = "ziggibot-uni"
    upstream_repo = "vaultbot"
    try:
        import subprocess

        vault_root = os.path.dirname(os.path.dirname(backend_dir))
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=vault_root,
            timeout=10,
        )
        if r.returncode == 0:
            m = re.search(
                r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", r.stdout.strip()
            )
            if m:
                upstream_owner, upstream_repo = m.group(1), m.group(2)
    except Exception:  # noqa: BLE001 — best-effort, falls back to defaults
        pass

    repo_path = f"repos/{upstream_owner}/{upstream_repo}"

    # 4. Dispatch on action.
    try:
        if action == "list":
            data = gh_api(
                "GET",
                f"{repo_path}/issues?state={state}&per_page=30",
                timeout=30,
            )
            # Issues endpoint also returns PRs; filter to true issues only.
            issues = [i for i in data if "pull_request" not in i]
            return {
                "status": "success",
                "count": len(issues),
                "issues": [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "state": i.get("state"),
                        "labels": [l.get("name") for l in i.get("labels", [])],
                        "comments": i.get("comments", 0),
                        "created_at": i.get("created_at"),
                        "html_url": i.get("html_url"),
                    }
                    for i in issues
                ],
            }

        if action == "read":
            if not issue_number:
                return {"error": "issue_number is required for 'read'"}
            issue = gh_api("GET", f"{repo_path}/issues/{issue_number}", timeout=30)
            comments = gh_api(
                "GET",
                f"{repo_path}/issues/{issue_number}/comments",
                timeout=30,
            )
            return {
                "status": "success",
                "number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "author": (issue.get("user") or {}).get("login"),
                "labels": [l.get("name") for l in issue.get("labels", [])],
                "body": issue.get("body") or "",
                "html_url": issue.get("html_url"),
                "comments": [
                    {
                        "author": (c.get("user") or {}).get("login"),
                        "body": c.get("body") or "",
                        "created_at": c.get("created_at"),
                    }
                    for c in comments
                ],
            }

        if action == "comment":
            if not issue_number:
                return {"error": "issue_number is required for 'comment'"}
            if not body:
                return {"error": "body is required for 'comment'"}
            data = gh_api(
                "POST",
                f"{repo_path}/issues/{issue_number}/comments",
                body={"body": body},
                timeout=30,
            )
            return {
                "status": "success",
                "comment_url": data.get("html_url"),
                "message": f"Comment posted to issue #{issue_number}",
            }

        if action == "close":
            if not issue_number:
                return {"error": "issue_number is required for 'close'"}
            if body:
                gh_api(
                    "POST",
                    f"{repo_path}/issues/{issue_number}/comments",
                    body={"body": body},
                    timeout=30,
                )
            data = gh_api(
                "PATCH",
                f"{repo_path}/issues/{issue_number}",
                body={"state": "closed"},
                timeout=30,
            )
            return {
                "status": "success",
                "state": data.get("state"),
                "message": f"Issue #{issue_number} closed",
            }

        if action == "label":
            if not issue_number:
                return {"error": "issue_number is required for 'label'"}
            if not labels:
                return {"error": "labels is required for 'label'"}
            data = gh_api(
                "POST",
                f"{repo_path}/issues/{issue_number}/labels",
                body={"labels": labels},
                timeout=30,
            )
            return {
                "status": "success",
                "labels": [l.get("name") for l in data],
                "message": f"Labels added to issue #{issue_number}",
            }

        return {"error": f"Unknown action: {action}"}

    except GhError as e:
        return {"error": str(e)}
