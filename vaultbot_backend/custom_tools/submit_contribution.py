"""
Agent-authored tool: submit_contribution

Supports two flows:
1. Direct push (user has write access to upstream) — push to origin, create PR
2. Fork-based push (user does NOT have write access) — fork upstream, push to
   fork, create cross-fork PR

The tool auto-detects which flow to use by checking the user's permissions
on the upstream repo via the GitHub API.
"""

SCHEMA = {
    "name": "submit_contribution",
    "description": (
        "Submit uncommitted changes as a GitHub pull request for community review. "
        "If the user has write access to the upstream repo, pushes directly and "
        "creates a PR. "
        "If not, forks the repo, pushes to the fork, and creates a cross-fork PR. "
        "Requires the gh CLI authenticated via 'gh auth login'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "Short title for the pull request (e.g. 'Fix subprocess "
                    "window popup on Windows')"
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Description of what the changes do and why. Will be used "
                    "as the PR body."
                ),
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of specific files to include. If omitted, "
                    "all uncommitted changes are included."
                ),
            },
            "skip_ci": {
                "type": "boolean",
                "description": (
                    "Skip the pre-flight CI gate check. Defaults to false — "
                    "the tool runs ruff check, ruff format --check, and "
                    "pytest unit tests locally and refuses to push if any "
                    "hard gate fails. Set to true only if you are certain "
                    "the changes are CI-clean."
                ),
            },
        },
        "required": ["title"],
    },
}


def _failed_gates(gates: dict) -> dict:
    """Return the subset of gate results whose status is fail/error.

    Pure helper — unit-testable without running any subprocess. A gate is
    "failed" if its status is "fail" (the check ran and reported a problem)
    or "error" (the check itself could not run). "pass" and "skipped" are
    not failures.
    """
    return {
        name: g for name, g in gates.items() if g.get("status") in ("fail", "error")
    }


def _run_preflight_ci_gates(vault_root: str) -> dict:
    """Run the CI hard gates locally before pushing a PR.

    Mirrors the hard gates in .github/workflows/ci.yml: ruff check (full
    rule set), ruff format --check, and pytest unit tests. Returns a dict
    keyed by gate name with a "status" (pass/fail/error/skipped) and an
    "output" tail. This is the enforcement mechanism that prevents VaultBot
    from submitting a PR that will fail CI on a mechanical lint/format/test
    error.
    """
    import os
    import shutil
    import sys

    from subprocess_utils import run as _subprocess_run

    backend_dir = os.path.join(vault_root, "vaultbot", "vaultbot_backend")
    if not os.path.isdir(backend_dir):
        backend_dir = vault_root

    # Locate ruff (venv first, then PATH).
    ruff_bin = None
    for candidate in (
        os.path.join(vault_root, ".venv", "Scripts", "ruff.exe"),
        os.path.join(vault_root, ".venv", "bin", "ruff"),
    ):
        if os.path.exists(candidate):
            ruff_bin = candidate
            break
    if ruff_bin is None:
        ruff_bin = shutil.which("ruff")

    # Locate the venv python for pytest.
    venv_python = None
    for candidate in (
        os.path.join(vault_root, ".venv", "Scripts", "python.exe"),
        os.path.join(vault_root, ".venv", "bin", "python"),
    ):
        if os.path.exists(candidate):
            venv_python = candidate
            break
    if venv_python is None:
        venv_python = sys.executable

    # Match the env CI sets for the pytest hard gate.
    test_env = dict(os.environ)
    test_env["VAULTBOT_SKIP_LOCK"] = "1"
    test_env["VAULTBOT_SKIP_WATCHER"] = "1"
    test_env["VAULT_PATH"] = vault_root

    gates: dict = {}

    # Gate 1: ruff check (full rule set) — HARD GATE
    if ruff_bin:
        try:
            r = _subprocess_run(
                [ruff_bin, "check", "."],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=backend_dir,
            )
            gates["ruff_check"] = {
                "status": "pass" if r.returncode == 0 else "fail",
                "output": (r.stdout + r.stderr)[-2000:],
            }
        except Exception as e:  # noqa: BLE001 — best-effort pre-flight gate
            gates["ruff_check"] = {"status": "error", "output": str(e)}
    else:
        gates["ruff_check"] = {"status": "skipped", "output": "ruff not found"}

    # Gate 2: ruff format --check — HARD GATE
    if ruff_bin:
        try:
            r = _subprocess_run(
                [ruff_bin, "format", "--check", "."],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=backend_dir,
            )
            gates["ruff_format"] = {
                "status": "pass" if r.returncode == 0 else "fail",
                "output": (r.stdout + r.stderr)[-2000:],
            }
        except Exception as e:  # noqa: BLE001 — best-effort pre-flight gate
            gates["ruff_format"] = {"status": "error", "output": str(e)}
    else:
        gates["ruff_format"] = {"status": "skipped", "output": "ruff not found"}

    # Gate 3: pytest unit tests — HARD GATE
    try:
        r = _subprocess_run(
            [
                venv_python,
                "-m",
                "pytest",
                "tests/",
                "-q",
                "-m",
                "unit",
                "--tb=short",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=backend_dir,
            env=test_env,
        )
        gates["pytest"] = {
            "status": "pass" if r.returncode == 0 else "fail",
            "output": (r.stdout + r.stderr)[-2000:],
        }
    except Exception as e:  # noqa: BLE001 — best-effort pre-flight gate
        gates["pytest"] = {"status": "error", "output": str(e)}

    return gates


def run(args: dict) -> dict:
    """Submit uncommitted changes as a GitHub pull request.

    Auto-detects whether to use direct-push or fork-based flow by checking
    the user's permissions on the upstream repo.

    Returns a dict with the PR URL on success, or an error message.
    """
    import os
    import sys
    import time

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from custom_tools.gh_client import GhError, gh_api, gh_available
    from subprocess_utils import run as _subprocess_run

    def run_git(git_args, cwd):
        try:
            r = _subprocess_run(
                ["git", *git_args],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=cwd,
            )
            return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            return False, "", str(e)

    title = args.get("title", "").strip()
    if not title:
        return {"error": "title is required"}

    description = args.get("description", "").strip()
    specific_files = args.get("files", [])

    # Find the vault root — the nearest directory containing .git, found
    # by walking up from vaultbot_backend/.  Previously this was hardcoded
    # as 2 levels up (backend -> vaultbot/ -> vault root), which only works
    # when the vault root IS the git repo.  When the git repo is one level
    # further up (e.g. vaultbot-fork/ containing vaultbot/), the old code
    # pointed at a directory with no .git and every git command failed.
    from custom_tools.upstream_identity import (
        UpstreamIdentityError,
        _find_git_root,
        _parse_github_url,
        resolve_upstream,
    )
    from workspace import WorkspaceError, workspace_registry

    try:
        selected_workspace = workspace_registry.get()
    except WorkspaceError as e:
        return {"error": str(e)}
    vault_root = (
        selected_workspace.local_root
        if selected_workspace is not None
        else _find_git_root(backend_dir)
    )
    if vault_root is None:
        return {
            "error": (
                "Could not find a git repository. The vault root must be "
                "inside a git repo (one with a .git directory). "
                "Run 'git init' or clone the repo so that git operations "
                "like add/commit/push work."
            )
        }

    # 1. Check for gh CLI (auth is handled by gh auth login, not a token)
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

    # 1b. Check if contributions are allowed (opt-in)
    from live_config import allow_contributions

    if not allow_contributions():
        return {
            "error": "Contributions are not enabled.",
            "hint": (
                "Enable 'Allow contributions' in VaultBot settings (under "
                "Community contributions), "
                "or ask your operator to enable it. This is an opt-in feature "
                "— VaultBot will never submit PRs without explicit permission."
            ),
        }

    # 2. Determine upstream repo — single source of truth
    #    (env vars > git remote > loud error; no silent hardcoded fallback)

    try:
        upstream_owner, upstream_repo = resolve_upstream()
    except UpstreamIdentityError as e:
        return {"error": str(e)}

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
                        "error": (
                            f"Refusing to commit: sensitive file would be staged: {pat}"
                        ),
                        "hint": (
                            "Check your .gitignore. This file should not be committed."
                        ),
                    }

    # 5b. Baseline-marker filter — exclude non-baseline System/ .md files.
    # Only .md files under vaultbot-stuff/System/ are checked; .py files
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

    _SYSTEM_PREFIX = "vaultbot-stuff/System/"
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
        except Exception:  # noqa: BLE001 — best-effort: unreadable file is excluded from PR
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
                "vaultbot-stuff/System/ .md file you want to share. "
                "Backend .py files are always baseline and don't need a marker. "
                "See CONTRIBUTING.md → Baseline markers for the full policy."
            ),
            "excluded_files": _excluded,
        }

    # 5c. Pre-flight CI gates — refuse to push a PR that will fail CI.
    # This is the enforcement mechanism that prevents VaultBot from
    # submitting a PR that fails the CI hard gates on a mechanical
    # lint/format/test error (see issue #80). Runs ruff check, ruff
    # format --check, and pytest unit tests locally — the same hard gates
    # .github/workflows/ci.yml runs — and blocks the push if any fail.
    # skip_ci=true bypasses this (for maintainers who know the tree is clean).
    skip_ci = bool(args.get("skip_ci", False))
    if not skip_ci:
        _gates = _run_preflight_ci_gates(vault_root)
        _failed = _failed_gates(_gates)
        if _failed:
            _detail = "\n\n".join(
                f"### {name}\n{g.get('output', '').strip()}"
                for name, g in _failed.items()
            )
            return {
                "error": (
                    "Pre-flight CI gates failed. Refusing to submit a PR "
                    "that would fail CI."
                ),
                "failed_gates": list(_failed.keys()),
                "detail": _detail,
                "hint": (
                    "Fix the failures above, then re-run submit_contribution. "
                    "Run the Run-CI-Gates procedure to reproduce them locally. "
                    "If you are certain the changes are CI-clean, pass "
                    "skip_ci=true to bypass this check."
                ),
            }

    # 6. Stage changes (only the baseline-filtered set)
    for f in _filtered_files:
        ok, _, err = run_git(["add", f], vault_root)
        if not ok:
            return {"error": f"Could not stage file {f}: {err}"}

    ok, diff_stat, _ = run_git(["diff", "--cached", "--stat"], vault_root)
    if not diff_stat.strip():
        return {
            "error": (
                "No staged changes to submit. Make your changes first, then "
                "run this tool."
            )
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

    # 8b. Rebase onto the latest upstream main to avoid stale-branch conflicts.
    # The contribution branch is created from the local main, which may be
    # behind the upstream repo (e.g. a directory rename landed upstream after
    # this clone was made — see the vault/ -> myvault/ rename that broke PR
    # #269). Rebase onto the latest upstream main so the PR doesn't conflict
    # on merge. If the rebase conflicts, abort and surface it loudly rather
    # than pushing a PR that can't merge.
    upstream_url = f"https://github.com/{upstream_owner}/{upstream_repo}.git"
    ok, _, fetch_err = run_git(["fetch", upstream_url, "main"], vault_root)
    if ok:
        ok, _, rebase_err = run_git(["rebase", "FETCH_HEAD"], vault_root)
        if not ok:
            run_git(["rebase", "--abort"], vault_root)
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {
                "error": (
                    "Could not rebase the contribution branch onto the latest "
                    "upstream main. Your local main is stale relative to "
                    f"{upstream_owner}/{upstream_repo}. Sync it (git pull) and "
                    "re-run, or resolve the conflict manually."
                ),
                "detail": rebase_err,
            }
    else:
        print(
            f"[submit_contribution] Warning: could not fetch upstream main "
            f"({fetch_err}); pushing without rebasing. The PR may conflict.",
            file=sys.stderr,
        )

    # 9. Push — fork-based or direct
    if has_push_access:
        # === DIRECT PUSH FLOW (user has write access) ===
        ok, _push_out, push_err = run_git(
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
        # The user's fork is where we push.  In the common case the user
        # already has 'origin' pointing at their fork (the standard
        # fork-and-clone setup), so we can push to origin directly.  If
        # origin points somewhere else, we need a 'fork' remote.
        fork_url = f"https://github.com/{gh_username}/{upstream_repo}.git"

        # Check if origin is already the user's fork
        ok, origin_url, _ = run_git(["remote", "get-url", "origin"], vault_root)
        origin_is_fork = ok and _parse_github_url(origin_url) == (
            gh_username,
            upstream_repo,
        )

        if origin_is_fork:
            # 9a. Fork the upstream repo (no-op if fork already exists)
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

            # 9b. Push to origin (which is the fork)
            ok, _push_out, push_err = run_git(
                ["push", "-u", "origin", branch_name], vault_root
            )
            push_remote = "origin"
        else:
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
            ok, _, _ = run_git(["remote", "get-url", "fork"], vault_root)
            if ok:
                run_git(["remote", "set-url", "fork", fork_url], vault_root)
            else:
                run_git(["remote", "add", "fork", fork_url], vault_root)

            # 9d. Push to fork
            ok, _push_out, push_err = run_git(
                ["push", "-u", "fork", branch_name], vault_root
            )
            push_remote = "fork"

        if not ok:
            time.sleep(10)
            ok, _push_out, push_err = run_git(
                ["push", "-u", push_remote, branch_name], vault_root
            )
        if not ok:
            run_git(["checkout", "main"], vault_root)
            run_git(["branch", "-D", branch_name], vault_root)
            return {
                "error": f"Could not push to {push_remote}: {push_err}",
                "hint": (
                    f"Make sure your fork exists at {fork_url} and your gh "
                    f"auth has push access to it."
                ),
            }

        pr_head = f"{gh_username}:{branch_name}"

    # 10. Create PR via GitHub API
    from custom_tools.gh_client import get_instance_id

    instance_id = get_instance_id()
    attribution = f"*Submitted by: @{gh_username}*"
    if instance_id:
        attribution += f"\n*Instance: `{instance_id}`*"
    pr_body = f"""## Community Contribution

{description or "No description provided."}

### Changed files
```
{diff_stat}
```

---
*This PR was submitted via VaultBot's community contribution system.*
{attribution}
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
                f"https://github.com/{upstream_owner}/{upstream_repo}/"
                f"compare/main...{pr_head.replace(':', '-')}"
            ),
        }
