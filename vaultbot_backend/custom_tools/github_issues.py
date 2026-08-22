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
  - ``comment`` / ``close`` / ``label`` / ``create`` MUTATE the repo and are
    blocked in Safe Mode (they require Developer Mode). This keeps a
    non-technical user's VaultBot from mutating GitHub while still letting
    it *see* issues.

CONTRIBUTIONS GATE (opt-in, mirrors submit_contribution):
  The entire tool is gated behind ``VAULTBOT_ALLOW_CONTRIBUTIONS=true``. If
  the setting is off, every action refuses — including ``list`` / ``read``.
  This keeps the tool out of the LLM's context when the user hasn't opted
  into community contributions (no context bloat from a tool that will
  never be used). The load-time gate in ``self_improver.load_custom_tools``
  additionally prevents the schema from being advertised at all.
"""

SCHEMA = {
    "name": "github_issues",
    "description": (
        "Read and act on GitHub issues for the VaultBot repo. Actions: "
        "'list' (open issues), 'read' (issue body + comments), 'comment' "
        "(post a comment), 'close' (close an issue with a comment), "
        "'label' (add/remove labels), 'create' (open a new issue). "
        "'list' and 'read' are read-only; 'comment', 'close', 'label', and "
        "'create' mutate the repo and require Developer Mode. The entire "
        "tool requires the 'Allow contributions' setting to be enabled. "
        "Requires the gh CLI authenticated via 'gh auth login'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "comment", "close", "label", "create"],
                "description": "Which action to perform.",
            },
            "issue_number": {
                "type": "integer",
                "description": ("Issue number. Required for read/comment/close/label."),
            },
            "title": {
                "type": "string",
                "description": "Title for the new issue (for 'create').",
            },
            "body": {
                "type": "string",
                "description": (
                    "Comment text (for 'comment'), closing note (for "
                    "'close'), or issue body (for 'create')."
                ),
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
_MUTATING_ACTIONS = frozenset({"comment", "close", "label", "create"})


def run(args: dict) -> dict:
    """Read or act on GitHub issues via the gh CLI.

    Returns a dict with the action result, or an error message.
    """
    import os
    import sys

    # Add backend to path for gh_client.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from custom_tools.gh_client import GhError, gh_api, gh_available

    action = args.get("action", "").strip()
    if not action:
        return {"error": "action is required (list/read/comment/close/label)"}

    issue_number = args.get("issue_number")
    title = args.get("title", "").strip()
    body = args.get("body", "").strip()
    labels = args.get("labels", [])
    state = args.get("state", "open")

    # 1. gh CLI must be available + authenticated.
    if not gh_available():
        return {
            "error": "gh CLI not found or not authenticated.",
            "hint": (
                "Sign in to GitHub from the VaultBot settings panel "
                "(Settings → Community plugins → VaultBot → gear icon → "
                "'Sign in to GitHub'). This walks you through the one-time "
                "sign-in in your browser — no terminal needed."
            ),
        }

    # 1b. Contributions opt-in gate (mirrors submit_contribution). The entire
    #     tool is disabled when the user hasn't opted in — including read
    #     actions — so the tool never appears in a vault where contributions
    #     are off. The load-time gate in self_improver.load_custom_tools also
    #     keeps the schema out of the LLM context, but this call-time check is
    #     a defence-in-depth for direct/programmatic calls.
    allow_contributions = (
        os.environ.get("VAULTBOT_ALLOW_CONTRIBUTIONS", "").strip().lower()
    )
    if allow_contributions != "true":
        return {
            "error": "Contributions are not enabled.",
            "hint": (
                "Enable 'Allow contributions' in VaultBot settings (under "
                "Community contributions), or ask your operator to enable "
                "it. This is an opt-in feature — VaultBot will never read or "
                "mutate GitHub issues without explicit permission."
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

    # 3. Determine upstream repo — single source of truth
    #    (env vars > git remote > loud error; no silent hardcoded fallback)
    from custom_tools.upstream_identity import UpstreamIdentityError, resolve_upstream

    try:
        upstream_owner, upstream_repo = resolve_upstream(backend_dir)
    except UpstreamIdentityError as e:
        return {"error": str(e)}

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
                        "labels": [lbl.get("name") for lbl in i.get("labels", [])],
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
                "labels": [lbl.get("name") for lbl in issue.get("labels", [])],
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
                "labels": [lbl.get("name") for lbl in data],
                "message": f"Labels added to issue #{issue_number}",
            }

        if action == "create":
            if not title:
                return {"error": "title is required for 'create'"}
            from custom_tools.gh_client import get_instance_id

            instance_id = get_instance_id()
            issue_body = body or ""
            if instance_id:
                attribution = f"\n\n---\n*Filed by VaultBot instance `{instance_id}`.*"
                issue_body = (
                    (issue_body + attribution) if issue_body else attribution.strip()
                )
            data = gh_api(
                "POST",
                f"{repo_path}/issues",
                body={"title": title, "body": issue_body},
                timeout=30,
            )
            return {
                "status": "success",
                "number": data.get("number"),
                "title": data.get("title"),
                "html_url": data.get("html_url"),
                "message": f"Issue #{data.get('number')} created",
            }

        return {"error": f"Unknown action: {action}"}

    except GhError as e:
        return {"error": str(e)}
