"""Agent-authored tool: pr_feedback

The missing feedback loop for the contributor's side of the community-
contribution system. ``submit_contribution`` pushes a PR and returns the
URL, but after that the contributor's VaultBot is flying blind — it has no
way to see whether CI passed, what ruff/pytest errors broke the build, what
the maintainer said in review comments, or whether the PR was merged.

This tool closes that loop. It fetches, for a given PR number:

  1. **PR state** — open / closed / merged, plus mergeable status.
  2. **CI check-runs** — for each check: name, status, conclusion, and a
     URL to the full log.
  3. **Failed-check annotations** — the actual error messages (ruff
     violations, test failures) extracted from the check-run annotations
     API, so the LLM can see *what* to fix without scraping raw logs.
  4. **Review comments** — both issue-level comments and inline code review
     comments, so the contributor can see the maintainer's feedback.

All actions are READ-ONLY — this tool never mutates the repo. It is gated
behind ``VAULTBOT_ALLOW_CONTRIBUTIONS=true`` (like the other contribution
tools) and skipped at load time when contributions are off (see
``self_improver._CONTRIBUTIONS_GATED_TOOLS``).

Auth is handled by ``gh auth login`` (no token management) via the shared
``gh_client.gh_api`` helper.
"""

SCHEMA = {
    "name": "pr_feedback",
    "description": (
        "Check the status of a pull request you submitted — CI check results, "
        "failure annotations, review comments, and merge state. This is the "
        "contributor's feedback loop: after submit_contribution pushes a PR, "
        "use this to see whether CI passed, what errors to fix, and what the "
        "maintainer said. Read-only — never mutates the repo. Requires the "
        "gh CLI authenticated via 'gh auth login' and the 'Allow "
        "contributions' setting enabled."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pr_number": {
                "type": "integer",
                "description": "The PR number to check.",
            },
        },
        "required": ["pr_number"],
    },
}


def run(args: dict) -> dict:
    """Fetch PR feedback: state, CI checks, annotations, and review comments.

    Returns a dict with the PR's current state, a list of CI check-runs
    (with conclusions + URLs), failed-check annotations (the actual error
    messages), and review comments. Read-only.
    """
    import os
    import re
    import sys

    # Add backend to path for gh_client + upstream_identity.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from custom_tools.gh_client import GhError, gh_api, gh_available

    pr_number = args.get("pr_number")
    if not pr_number:
        return {"error": "pr_number is required"}

    # 1. gh CLI must be available + authenticated.
    if not gh_available():
        return {
            "error": "gh CLI not found or not authenticated.",
            "hint": (
                "Install the GitHub CLI from https://cli.github.com and run "
                "'gh auth login' to sign in."
            ),
        }

    # 2. Contributions opt-in gate (mirrors submit_contribution / github_issues).
    allow_contributions = (
        os.environ.get("VAULTBOT_ALLOW_CONTRIBUTIONS", "").strip().lower()
    )
    if allow_contributions != "true":
        return {
            "error": "Contributions are not enabled.",
            "hint": (
                "Enable 'Allow contributions' in VaultBot settings (under "
                "Community contributions), or ask your operator to enable it."
            ),
        }

    # 3. Determine upstream repo (owner/repo) — single source of truth.
    #    Uses upstream_identity (env vars > git remote > loud error), the
    #    same as the sibling contribution tools. Previously this hardcoded
    #    a Ziggibot0/vaultbot fallback and only parsed the git remote,
    #    bypassing upstream_identity — a repo-agnostic regression.
    from custom_tools.upstream_identity import (
        UpstreamIdentityError,
        resolve_upstream,
    )

    try:
        upstream_owner, upstream_repo = resolve_upstream()
    except UpstreamIdentityError as e:
        return {"error": str(e)}

    repo_path = f"repos/{upstream_owner}/{upstream_repo}"

    try:
        # 4. Fetch PR metadata.
        pr = gh_api("GET", f"{repo_path}/pulls/{pr_number}", timeout=30)
        head_sha = (pr.get("head") or {}).get("sha", "")
        merged = pr.get("merged", False)
        state = pr.get("state", "unknown")
        mergeable = pr.get("mergeable")

        result: dict = {
            "pr_number": pr_number,
            "title": pr.get("title", ""),
            "state": state,
            "merged": merged,
            "mergeable": mergeable,
            "url": pr.get("html_url", ""),
            "head_branch": (pr.get("head") or {}).get("ref", ""),
            "head_sha": head_sha,
            "additions": pr.get("additions", 0),
            "deletions": pr.get("deletions", 0),
            "ci_checks": [],
            "ci_summary": "unknown",
            "failed_annotations": [],
            "review_comments": [],
            "issue_comments": [],
        }

        # 5. Fetch CI check-runs for the head commit.
        if head_sha:
            try:
                check_runs = gh_api(
                    "GET",
                    f"{repo_path}/commits/{head_sha}/check-runs",
                    timeout=30,
                )
                runs = (
                    check_runs.get("check_runs", [])
                    if isinstance(check_runs, dict)
                    else []
                )
                if not runs:
                    result["ci_summary"] = "none"
                    result["ci_detail"] = "No check-runs found for this commit."
                else:
                    conclusions = [r.get("conclusion") for r in runs]
                    statuses = [r.get("status") for r in runs]
                    if any(s in ("queued", "in_progress", "pending") for s in statuses):
                        result["ci_summary"] = "pending"
                        result["ci_detail"] = (
                            "One or more check-runs are still running."
                        )
                    elif any(
                        c
                        in (
                            "failure",
                            "cancelled",
                            "timed_out",
                            "action_required",
                        )
                        for c in conclusions
                    ):
                        result["ci_summary"] = "failure"
                        failed_names = [
                            r.get("name")
                            for r in runs
                            if r.get("conclusion")
                            in (
                                "failure",
                                "cancelled",
                                "timed_out",
                                "action_required",
                            )
                        ]
                        result["ci_detail"] = (
                            f"Failing check-runs: {', '.join(failed_names)}"
                        )
                    elif all(c == "success" for c in conclusions):
                        result["ci_summary"] = "success"
                        result["ci_detail"] = f"{len(runs)} check-run(s) passed."
                    else:
                        result["ci_summary"] = "unknown"
                        result["ci_detail"] = f"Conclusions: {conclusions}"

                    result["ci_checks"] = [
                        {
                            "name": r.get("name"),
                            "status": r.get("status"),
                            "conclusion": r.get("conclusion"),
                            "url": r.get("html_url"),
                        }
                        for r in runs
                    ]
            except GhError as e:
                result["ci_summary"] = "error"
                result["ci_detail"] = f"Failed to fetch check-runs: {e}"

        # 6. Fetch annotations for failed check-runs — these are the actual
        #    error messages (ruff violations, test failures) that the LLM
        #    needs to see to fix the PR. The GitHub annotations API returns
        #    them per check-run.
        for check in result["ci_checks"]:
            if check.get("conclusion") not in (
                "failure",
                "cancelled",
                "timed_out",
            ):
                continue
            # The check-runs API response includes a self-link to
            # annotations; we fetch via the check-runs/{id}/annotations
            # endpoint.
            check_url = check.get("url", "")
            # Extract the check-run ID from the URL (last numeric segment).
            id_match = re.search(r"/(\d+)(?:/annotations)?$", check_url)
            if not id_match:
                continue
            check_run_id = id_match.group(1)
            try:
                annotations = gh_api(
                    "GET",
                    f"{repo_path}/check-runs/{check_run_id}/annotations",
                    timeout=15,
                )
                if isinstance(annotations, list):
                    for ann in annotations:
                        result["failed_annotations"].append(
                            {
                                "check": check.get("name"),
                                "level": ann.get("annotation_level"),
                                "message": ann.get("message"),
                                "path": ann.get("path"),
                                "start_line": (
                                    ann.get("start_line")
                                    or (ann.get("location") or {}).get("start_line")
                                ),
                                "url": ann.get("html_url"),
                            }
                        )
            except GhError:
                # Annotations may not be available for all check-run types
                # (e.g. legacy CI). Skip silently — the check-run URL is
                # already in ci_checks for manual log inspection.
                pass

        # 7. Fetch issue-level comments (the PR conversation timeline).
        try:
            comments = gh_api(
                "GET",
                f"{repo_path}/issues/{pr_number}/comments",
                timeout=30,
            )
            result["issue_comments"] = [
                {
                    "author": (c.get("user") or {}).get("login"),
                    "body": c.get("body") or "",
                    "created_at": c.get("created_at"),
                }
                for c in comments
            ]
        except GhError:
            pass  # non-blocking — comments are supplementary

        # 8. Fetch inline code review comments.
        try:
            review_comments = gh_api(
                "GET",
                f"{repo_path}/pulls/{pr_number}/comments",
                timeout=30,
            )
            result["review_comments"] = [
                {
                    "author": (c.get("user") or {}).get("login"),
                    "body": c.get("body") or "",
                    "path": c.get("path"),
                    "line": c.get("line") or c.get("original_line"),
                    "created_at": c.get("created_at"),
                }
                for c in review_comments
            ]
        except GhError:
            pass  # non-blocking

        return result

    except GhError as e:
        return {"error": str(e)}
