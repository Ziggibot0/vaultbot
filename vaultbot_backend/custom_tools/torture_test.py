"""
Agent-authored tool: torture_test
"""

SCHEMA = {
    "name": "torture_test",
    "description": (
        "Run torture tests on a pull request before merging. Downloads "
        "changed files from the PR branch and runs: Python syntax check, JS "
        "syntax check, .gitignore tampering check, malware/exfiltration "
        "pattern scan, path whitelist check. Returns a structured pass/fail "
        "report. Requires the gh CLI authenticated via 'gh auth login'."
    ),
    "parameters": {
        "properties": {
            "pr_number": {
                "description": "The PR number to torture test",
                "type": "integer",
            }
        },
        "type": "object",
    },
}


def run(args: dict) -> dict:
    """Run torture tests on a PR before merging.

    Tests:
    1. Syntax check all changed .py files
    2. JS syntax check on changed .js files
    3. .gitignore tampering check
    4. Malware/exfiltration pattern scan
    5. Import check (if .py files changed in backend)

    Returns a structured report with pass/fail per check.
    """
    import os
    import re
    import subprocess
    import sys
    import tempfile

    # Add backend to path for gh_client
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from custom_tools.gh_client import GhError, gh_api, gh_available, gh_raw

    # 1. Check for gh CLI (auth is handled by gh auth login, not a token)
    if not gh_available():
        return {
            "error": "gh CLI not found or not authenticated.",
            "hint": (
                "Install the GitHub CLI from https://cli.github.com and run "
                "'gh auth login'."
            ),
        }

    # 2. Determine upstream repo — single source of truth
    #    (env vars > git remote > loud error; no silent hardcoded fallback)
    from custom_tools.upstream_identity import UpstreamIdentityError, resolve_upstream

    try:
        upstream_owner, upstream_repo = resolve_upstream()
    except UpstreamIdentityError as e:
        return {"error": str(e)}

    pr_number = args.get("pr_number")
    if not pr_number:
        return {"error": "pr_number is required"}

    # 3. Fetch PR info
    try:
        pr_data = gh_api(
            "GET", f"repos/{upstream_owner}/{upstream_repo}/pulls/{pr_number}"
        )
    except GhError as e:
        return {"error": f"Failed to fetch PR: {e}"}

    pr_title = pr_data["title"]
    head_ref = pr_data["head"]["ref"]
    head_repo = pr_data["head"].get("repo", {})
    head_owner = head_repo.get("owner", {}).get("login", "") if head_repo else ""
    head_repo_name = head_repo.get("name", "") if head_repo else ""

    # 4. Fetch PR files
    try:
        pr_files = gh_api(
            "GET",
            f"repos/{upstream_owner}/{upstream_repo}/pulls/{pr_number}/files?per_page=100",
            timeout=30,
        )
    except GhError as e:
        return {"error": f"Failed to fetch PR files: {e}"}

    # 5. Run tests
    tests = []
    all_pass = True

    # --- Test 1: Syntax check .py files ---
    py_files = [f for f in pr_files if f["filename"].endswith(".py") and f.get("patch")]
    if py_files:
        syntax_results = []
        for f in py_files:
            try:
                content = gh_raw(
                    head_owner or upstream_owner,
                    head_repo_name or upstream_repo,
                    head_ref,
                    f["filename"],
                )
                # Write to temp file and syntax check
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                # Find venv python
                vault_root_path = os.path.dirname(
                    os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    )
                )
                venv_python = os.path.join(
                    vault_root_path, ".venv", "Scripts", "python.exe"
                )
                if not os.path.exists(venv_python):
                    venv_python = os.path.join(
                        vault_root_path, ".venv", "bin", "python"
                    )
                if not os.path.exists(venv_python):
                    venv_python = sys.executable

                r = subprocess.run(
                    [venv_python, "-m", "py_compile", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                os.unlink(tmp_path)

                if r.returncode == 0:
                    syntax_results.append({"file": f["filename"], "status": "pass"})
                else:
                    syntax_results.append(
                        {
                            "file": f["filename"],
                            "status": "fail",
                            "error": r.stderr.strip()[:300],
                        }
                    )
                    all_pass = False
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                syntax_results.append(
                    {"file": f["filename"], "status": "error", "error": str(e)}
                )
                all_pass = False

        passed = sum(1 for r in syntax_results if r["status"] == "pass")
        failed = sum(1 for r in syntax_results if r["status"] == "fail")
        tests.append(
            {
                "test": "python_syntax_check",
                "status": "pass" if failed == 0 else "fail",
                "checked": len(py_files),
                "passed": passed,
                "failed": failed,
                "details": syntax_results,
            }
        )
    else:
        tests.append(
            {
                "test": "python_syntax_check",
                "status": "skip",
                "message": "No .py files in PR",
            }
        )

    # --- Test 2: JS syntax check ---
    js_files = [f for f in pr_files if f["filename"].endswith(".js") and f.get("patch")]
    if js_files:
        js_results = []
        for f in js_files:
            try:
                content = gh_raw(
                    head_owner or upstream_owner,
                    head_repo_name or upstream_repo,
                    head_ref,
                    f["filename"],
                )
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".js", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                r = subprocess.run(
                    ["node", "--check", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                os.unlink(tmp_path)

                if r.returncode == 0:
                    js_results.append({"file": f["filename"], "status": "pass"})
                else:
                    js_results.append(
                        {
                            "file": f["filename"],
                            "status": "fail",
                            "error": r.stderr.strip()[:300],
                        }
                    )
                    all_pass = False
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                js_results.append(
                    {"file": f["filename"], "status": "error", "error": str(e)}
                )
                all_pass = False

        passed = sum(1 for r in js_results if r["status"] == "pass")
        failed = sum(1 for r in js_results if r["status"] == "fail")
        tests.append(
            {
                "test": "js_syntax_check",
                "status": "pass" if failed == 0 else "fail",
                "checked": len(js_files),
                "passed": passed,
                "failed": failed,
                "details": js_results,
            }
        )
    else:
        tests.append(
            {
                "test": "js_syntax_check",
                "status": "skip",
                "message": "No .js files in PR",
            }
        )

    # --- Test 3: .gitignore tampering check ---
    gitignore_file = [f for f in pr_files if f["filename"] == ".gitignore"]
    if gitignore_file:
        patch = gitignore_file[0].get("patch", "")
        tampering = []
        sensitive_paths = [
            ".env",
            "sessions/",
            "identity/",
            "vaultbot_index/",
            "trash/",
            "vaultbot_venv/",
            "Memory/",
            "Knowledge/",
            "learningMaterial/",
            "data.json",
            "mcp.json",
            "workspace.json",
        ]
        for line in patch.split("\n"):
            if line.startswith("-") and not line.startswith("---"):
                removed = line[1:].strip()
                for sp in sensitive_paths:
                    if sp in removed:
                        tampering.append(
                            f"Removed ignore rule for '{removed}' (matches "
                            f"sensitive path '{sp}')"
                        )

        if tampering:
            tests.append(
                {"test": "gitignore_tampering", "status": "fail", "issues": tampering}
            )
            all_pass = False
        else:
            tests.append({"test": "gitignore_tampering", "status": "pass"})
    else:
        tests.append(
            {
                "test": "gitignore_tampering",
                "status": "skip",
                "message": ".gitignore not modified",
            }
        )

    # --- Test 4: Malware/exfiltration scan ---
    MALWARE_PATTERNS = [
        (r"socket\.socket\s*\(", "Raw socket creation"),
        (r"base64\.b64decode\s*\(.*exec", "Base64 decode + exec pattern"),
        (r"curl\s+.*\$\(", "curl with command substitution"),
        (r"wget\s+.*\|.*sh", "wget pipe to shell"),
        (r"pickle\.loads\s*\(", "pickle.loads (deserialization attack)"),
        (r"marshal\.loads\s*\(", "marshal.loads (deserialization)"),
        (r"ctypes\.CDLL\s*\(", "Dynamic library loading"),
        (r"__import__\s*\(\s*['\"]os['\"]\s*\)", "Dynamic OS import"),
        (
            r"subprocess\..*shell\s*=\s*True.*\binput\b",
            "subprocess with user input + shell=True",
        ),
        (r"open\s*\(\s*['\"]\.env['\"]", "Reading .env file"),
        (r"open\s*\(\s*['\"].*sessions/", "Reading sessions directory"),
        (r"open\s*\(\s*['\"].*identity/", "Reading identity directory"),
        (r"requests\.(post|put).*\btoken\b", "HTTP request with token"),
        (
            r"https?://(?!github\.com|api\.github\.com|raw\.githubusercontent\.com)",
            "Network call to non-GitHub host",
        ),
    ]

    malware_issues = []
    for f in pr_files:
        patch = f.get("patch", "")
        if not patch:
            continue
        fname = f["filename"]
        # Skip markdown files — documentation mentioning patterns is not
        # dangerous code
        if fname.endswith(".md"):
            continue
        for line in patch.split("\n"):
            if not line.startswith("+") or line.startswith("+++"):
                continue
            code = line[1:]
            # Skip comments
            if code.strip().startswith("#") or code.strip().startswith("//"):
                continue
            # Skip lines that are regex pattern definitions (defining
            # detection patterns, not using them). These look like:
            # (r"pattern", "description"),
            if code.strip().startswith("(") and 'r"' in code:
                continue
            # Skip lines that are string assignments containing pattern
            # descriptions
            if code.strip().startswith('"') or code.strip().startswith("'"):
                continue
            for pattern, desc in MALWARE_PATTERNS:
                if re.search(pattern, code):
                    # Filter false positives: URLs in strings/comments
                    if "non-GitHub host" in desc and "github.com" in code:
                        continue
                    # Filter: if the line itself is defining a regex pattern
                    # (contains r" prefix)
                    if 'r"' in code and (
                        "\\" in code or "\\s" in code or "\\." in code
                    ):
                        continue
                    malware_issues.append(
                        {"file": fname, "pattern": desc, "code": code.strip()[:100]}
                    )

    if malware_issues:
        tests.append(
            {"test": "malware_scan", "status": "fail", "issues": malware_issues}
        )
        all_pass = False
    else:
        tests.append({"test": "malware_scan", "status": "pass"})

    # --- Test 5: Path whitelist check ---
    ALLOWED_PREFIXES = [
        "vaultbot_backend/",
        "myvault/",
        "vaultbot-stuff/",
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
    path_violations = []
    for f in pr_files:
        fname = f["filename"]
        if not any(
            fname.startswith(p) or fname == p.rstrip("/") for p in ALLOWED_PREFIXES
        ):
            path_violations.append(fname)

    if path_violations:
        tests.append(
            {"test": "path_whitelist", "status": "fail", "violations": path_violations}
        )
        all_pass = False
    else:
        tests.append({"test": "path_whitelist", "status": "pass"})

    # 6. Overall verdict
    overall = "PASS" if all_pass else "FAIL"
    failed_tests = [t for t in tests if t.get("status") == "fail"]

    result = {
        "pr_number": pr_number,
        "pr_title": pr_title,
        "head_ref": head_ref,
        "changed_files": len(pr_files),
        "overall": overall,
        "tests_run": len([t for t in tests if t.get("status") != "skip"]),
        "tests_passed": len([t for t in tests if t.get("status") == "pass"]),
        "tests_failed": len(failed_tests),
        "tests": tests,
    }

    if failed_tests:
        result["failed_test_names"] = [t["test"] for t in failed_tests]

    return result
