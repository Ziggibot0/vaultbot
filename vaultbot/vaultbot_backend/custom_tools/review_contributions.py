"""
Agent-authored tool: review_contributions
"""

SCHEMA = {
    "name": "review_contributions",
    "description": "List and review open pull requests on the VaultBot GitHub repo. For each PR, fetches the diff, runs a safety scan (checks for secrets, dangerous code patterns, path traversal, .gitignore tampering), and returns a structured report. Requires the gh CLI authenticated via 'gh auth login'.",
    "parameters": {
        "properties": {
            "merge": {
                "description": "If true and the PR passes all safety checks, merge it after reviewing. Default: false (review only).",
                "type": "boolean",
            },
            "pr_number": {
                "description": "Optional: review a specific PR by number. If omitted, reviews all open PRs.",
                "type": "integer",
            },
        },
        "type": "object",
    },
}


def run(args: dict) -> dict:
    """List and review open PRs on the VaultBot repo.

    For each PR:
    1. Fetch metadata (title, author, branch, changed files)
    2. Fetch the file diff
    3. Run safety scan on each changed file
    4. Return a structured report

    If merge=True and all checks pass, merges the PR.
    """
    import os
    import re
    import sys

    # Add backend to path for gh_client
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from custom_tools.gh_client import gh_api, gh_available, GhError

    # 1. Check for gh CLI (auth is handled by gh auth login, not a token)
    if not gh_available():
        return {
            "error": "gh CLI not found or not authenticated.",
            "hint": "Install the GitHub CLI from https://cli.github.com and run 'gh auth login'.",
        }

    # 2. Determine upstream repo
    upstream_owner = "ziggibot-uni"
    upstream_repo = "vaultbot"

    # Try to get it from git remote (in case it changes)
    try:
        import subprocess

        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        vault_root = os.path.dirname(os.path.dirname(backend_dir))
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=vault_root,
            timeout=10,
        )
        if r.returncode == 0:
            match = re.search(
                r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", r.stdout.strip()
            )
            if match:
                upstream_owner, upstream_repo = match.group(1), match.group(2)
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass  # Fall back to defaults

    pr_number = args.get("pr_number")
    do_merge = args.get("merge", False)

    # 3. Get PRs to review
    if pr_number:
        # Review specific PR
        try:
            prs = [gh_api("GET", f"repos/{upstream_owner}/{upstream_repo}/pulls/{pr_number}")]
        except GhError as e:
            return {"error": f"Failed to fetch PR #{pr_number}: {e}"}
    else:
        # List all open PRs
        try:
            prs = gh_api(
                "GET",
                f"repos/{upstream_owner}/{upstream_repo}/pulls?state=open&per_page=30",
            )
        except GhError as e:
            return {"error": f"Failed to list PRs: {e}"}

    if not prs:
        return {"status": "success", "open_prs": 0, "message": "No open PRs to review."}

    # 4. Safety scan definitions
    DANGER_PATTERNS = [
        # Secrets
        (r"ghp_[A-Za-z0-9]{36}", "GitHub token"),
        (r"sk-[A-Za-z0-9]{40,}", "OpenAI API key"),
        (r"AKIA[A-Z0-9]{16}", "AWS access key"),
        (r"-----BEGIN (RSA |EC )?PRIVATE KEY-----", "Private key"),
        # Dangerous code
        (r"eval\s*\(", "eval() call"),
        (r"exec\s*\(", "exec() call"),
        (r"os\.system\s*\(", "os.system() call"),
        (r"__import__\s*\(", "__import__() call"),
        (r"subprocess\.Popen.*shell\s*=\s*True", "subprocess with shell=True"),
        # Data exfiltration
        (r"requests\.(post|put).*\.(env|env\b)", "HTTP request involving .env"),
        # Path traversal
        (r"\.\.\/\.\.\/\.\.\/", "Path traversal (../../..)"),
        # Personal data
        (r"C:\\Users\\[a-zA-Z]", "Hardcoded Windows user path"),
    ]

    ALLOWED_PATHS = [
        "vaultbot/",
        ".obsidian/plugins/vaultbot/",
        ".gitignore",
        "LICENSE",
        "README",
        "CONTRIBUTING",
        "SECURITY",
        "setup.ps1",
        "setup.sh",
        ".pre-commit",
        ".github/",
    ]

    SENSITIVE_FILES = [
        ".env",
        "data.json",
        "mcp.json",
        "workspace.json",
        ".venv/",
        "sessions/",
        "identity/",
        "vaultbot_index/",
        "trash/",
        "checkpoints/",
        "vaultbot_venv/",
        "Memory/",
        "Knowledge/",
        "learningMaterial/",
        "User/",
    ]

    def scan_file(file_info):
        """Scan a single changed file for safety issues."""
        issues = []
        filename = file_info.get("filename", "")
        patch = file_info.get("patch", "")
        file_info.get("status", "")

        # Check: file in allowed paths?
        path_allowed = any(
            filename.startswith(p) or filename == p.rstrip("/") for p in ALLOWED_PATHS
        )
        if not path_allowed:
            issues.append(
                {
                    "severity": "high",
                    "check": "path_whitelist",
                    "message": f"File '{filename}' is outside allowed contribution paths",
                }
            )

        # Check: sensitive file?
        for sf in SENSITIVE_FILES:
            if sf in filename:
                issues.append(
                    {
                        "severity": "critical",
                        "check": "sensitive_file",
                        "message": f"File '{filename}' matches sensitive pattern '{sf}'",
                    }
                )

        # Check: .gitignore modifications that un-ignore sensitive paths
        if filename == ".gitignore" and patch:
            for line in patch.split("\n"):
                # Lines starting with - in the patch are removals
                if line.startswith("-") and not line.startswith("---"):
                    removed = line[1:].strip()
                    # Check if a sensitive path is being un-ignored
                    for sf in SENSITIVE_FILES:
                        if sf in removed:
                            issues.append(
                                {
                                    "severity": "critical",
                                    "check": "gitignore_tampering",
                                    "message": f".gitignore removes ignore rule for '{removed}' (matches '{sf}')",
                                }
                            )

        # Check: danger patterns in patch (skip markdown — docs mentioning patterns are not dangerous)
        if patch and not filename.endswith(".md"):
            for pattern, desc in DANGER_PATTERNS:
                matches = re.findall(pattern, patch)
                if matches:
                    # Filter out false positives in comments and pattern definitions
                    real_matches = []
                    for line in patch.split("\n"):
                        if line.startswith("+") and not line.startswith("+++"):
                            code = line[1:]
                            if not code.strip().startswith(
                                "#"
                            ) and not code.strip().startswith("//"):
                                # Skip lines that are regex pattern definitions (defining detection patterns)
                                if 'r"' in code and (
                                    "\\" in code or "\\s" in code or "\\." in code
                                ):
                                    continue
                                if re.search(pattern, code):
                                    real_matches.append(code.strip()[:80])
                    if real_matches:
                        issues.append(
                            {
                                "severity": "high"
                                if "token" in desc.lower() or "key" in desc.lower()
                                else "medium",
                                "check": "danger_pattern",
                                "message": f"{desc} found in {filename}",
                                "evidence": real_matches[:3],
                            }
                        )

        # Check: binary or large file
        if file_info.get("additions", 0) > 5000:
            issues.append(
                {
                    "severity": "medium",
                    "check": "large_file",
                    "message": f"File '{filename}' has {file_info.get('additions', 0)} additions — unusually large",
                }
            )

        # Check: baseline marker for new System/ .md files.
        # New procedures and System notes must have "baseline: true" in
        # their YAML frontmatter. Modified files are checked for marker
        # removal. Backend .py files and root-level files are exempt.
        _SYSTEM_PREFIX = "vaultbot/System/"
        if filename.startswith(_SYSTEM_PREFIX) and filename.endswith(".md"):
            _status = file_info.get("status", "")
            if _status == "added":
                # New file — the patch contains the full content.
                # Check if the frontmatter has baseline: true.
                _has_baseline = False
                for _line in patch.split("\n"):
                    if _line.startswith("+") and not _line.startswith("+++"):
                        _stripped = _line[1:].strip()
                        if (
                            _stripped.lower().startswith("baseline:")
                            and "true" in _stripped.lower()
                        ):
                            _has_baseline = True
                            break
                if not _has_baseline:
                    issues.append(
                        {
                            "severity": "high",
                            "check": "missing_baseline_marker",
                            "message": (
                                f"New System/ file '{filename}' is missing "
                                f"'baseline: true' in its YAML frontmatter. "
                                f"Add it to mark this as shippable baseline content."
                            ),
                        }
                    )
            elif _status in ("modified", "renamed"):
                # Modified file — check if baseline: true was removed.
                _had_baseline = False
                _removed_baseline = False
                for _line in patch.split("\n"):
                    if _line.startswith("-") and not _line.startswith("---"):
                        _stripped = _line[1:].strip()
                        if (
                            _stripped.lower().startswith("baseline:")
                            and "true" in _stripped.lower()
                        ):
                            _removed_baseline = True
                    if _line.startswith("+") and not _line.startswith("+++"):
                        _stripped = _line[1:].strip()
                        if (
                            _stripped.lower().startswith("baseline:")
                            and "true" in _stripped.lower()
                        ):
                            _had_baseline = True
                if _removed_baseline and not _had_baseline:
                    issues.append(
                        {
                            "severity": "high",
                            "check": "baseline_marker_removed",
                            "message": (
                                f"'{filename}' had its 'baseline: true' marker "
                                f"removed. If this file is no longer shippable, "
                                f"explain why in the PR description."
                            ),
                        }
                    )

        return issues

    # 5. Review each PR
    results = []
    for pr in prs:
        pr_num = pr["number"]
        pr_title = pr["title"]
        pr_author = pr["user"]["login"]
        pr_url = pr["html_url"]
        head_ref = pr["head"]["ref"]
        head_repo = (
            pr["head"].get("repo", {}).get("full_name", "deleted")
            if pr["head"].get("repo")
            else "deleted"
        )

        # Fetch PR files
        try:
            files = gh_api(
                "GET",
                f"repos/{upstream_owner}/{upstream_repo}/pulls/{pr_num}/files",
                timeout=30,
            )
        except GhError as e:
            results.append(
                {
                    "pr_number": pr_num,
                    "title": pr_title,
                    "author": pr_author,
                    "url": pr_url,
                    "error": f"Failed to fetch files: {e}",
                }
            )
            continue

        # Scan each file
        all_issues = []
        file_summaries = []
        for f in files:
            issues = scan_file(f)
            all_issues.extend(issues)
            file_summaries.append(
                {
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "issues": len(issues),
                }
            )

        # Determine overall verdict
        critical = [i for i in all_issues if i["severity"] == "critical"]
        high = [i for i in all_issues if i["severity"] == "high"]
        medium = [i for i in all_issues if i["severity"] == "medium"]

        if critical:
            verdict = "REJECT"
            verdict_reason = f"{len(critical)} critical issue(s)"
        elif high:
            verdict = "REVIEW_MANUAL"
            verdict_reason = f"{len(high)} high-severity issue(s) need manual review"
        elif medium:
            verdict = "CAUTION"
            verdict_reason = f"{len(medium)} medium-severity issue(s)"
        else:
            verdict = "PASS"
            verdict_reason = "All safety checks passed"

        # --- CI gate: only merge when the PR's check-runs are green. ---
        # A stranger's PR can pass the safety scan but still break the
        # build (ruff + pytest). Fetch the head commit's check-runs and
        # require every completed run to be "success" before merging.
        ci_status = "unknown"
        ci_detail = ""
        head_sha = (pr.get("head") or {}).get("sha", "")
        if head_sha:
            try:
                check_runs = gh_api(
                    "GET",
                    f"repos/{upstream_owner}/{upstream_repo}/commits/{head_sha}/check-runs",
                    timeout=30,
                )
                runs = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
                if not runs:
                    ci_status = "none"
                    ci_detail = "No check-runs found for this commit."
                else:
                    conclusions = [r.get("conclusion") for r in runs]
                    statuses = [r.get("status") for r in runs]
                    if any(s in ("queued", "in_progress", "pending") for s in statuses):
                        ci_status = "pending"
                        ci_detail = "One or more check-runs are still running."
                    elif any(c in ("failure", "cancelled", "timed_out", "action_required") for c in conclusions):
                        ci_status = "failure"
                        failed = [r.get("name") for r in runs if r.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required")]
                        ci_detail = f"Failing check-runs: {', '.join(failed)}"
                    elif all(c == "success" for c in conclusions):
                        ci_status = "success"
                        ci_detail = f"{len(runs)} check-run(s) passed."
                    else:
                        ci_status = "unknown"
                        ci_detail = f"Conclusions: {conclusions}"
            except GhError as e:
                ci_status = "error"
                ci_detail = f"Failed to fetch check-runs: {e}"

        result = {
            "pr_number": pr_num,
            "title": pr_title,
            "author": pr_author,
            "url": pr_url,
            "head": head_ref,
            "head_repo": head_repo,
            "changed_files": len(files),
            "additions": pr.get("additions", 0),
            "deletions": pr.get("deletions", 0),
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "ci_status": ci_status,
            "ci_detail": ci_detail,
            "issues": all_issues,
            "files": file_summaries,
        }
        results.append(result)

        # If merging, require BOTH a PASS safety verdict AND green CI.
        # A pending/unknown CI status blocks the merge (fail-loud: never
        # merge a PR whose build state we can't confirm).
        if do_merge and verdict == "PASS" and ci_status == "success":
            try:
                merge_data = gh_api(
                    "PUT",
                    f"repos/{upstream_owner}/{upstream_repo}/pulls/{pr_num}/merge",
                    body={
                        "merge_method": "squash",
                        "commit_title": f"Merge PR #{pr_num}: {pr_title}",
                    },
                    timeout=30,
                )
                result["merged"] = True
                result["merge_message"] = merge_data.get("message", "Merged")
            except GhError as e:
                result["merged"] = False
                result["merge_error"] = str(e)
        elif do_merge and verdict == "PASS" and ci_status != "success":
            result["merged"] = False
            result["merge_error"] = (
                f"CI not green (status: {ci_status}). {ci_detail}"
            )

        # Post a comment with the review results
        comment_body = f"## 🤖 VaultBot Safety Review\n\n**Verdict:** {verdict}\n**Reason:** {verdict_reason}\n**CI:** {ci_status} — {ci_detail}\n\n"
        if all_issues:
            comment_body += "### Issues Found\n\n"
            for issue in all_issues:
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(
                    issue["severity"], "⚪"
                )
                comment_body += f"- {emoji} [{issue['severity']}] {issue['check']}: {issue['message']}\n"
                if issue.get("evidence"):
                    for ev in issue["evidence"]:
                        comment_body += f"  - `{ev}`\n"
        else:
            comment_body += "### ✅ No issues found\n\nAll safety checks passed.\n"

        if do_merge and verdict == "PASS":
            if result.get("merged"):
                comment_body += f"\n**Merged:** ✅ {result.get('merge_message', '')}\n"
            else:
                comment_body += (
                    f"\n**Merge blocked:** {result.get('merge_error', 'unknown')}\n"
                )

        comment_body += "\n---\n*Automated review by VaultBot safety scanner*"

        try:
            gh_api(
                "POST",
                f"repos/{upstream_owner}/{upstream_repo}/issues/{pr_num}/comments",
                body={"body": comment_body},
                timeout=15,
            )
        except GhError:
            pass  # Comment is best-effort

    return {
        "status": "success",
        "open_prs": len(prs),
        "results": results,
    }
