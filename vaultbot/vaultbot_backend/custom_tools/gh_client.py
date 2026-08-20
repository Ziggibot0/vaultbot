"""gh_client.py — shared GitHub CLI wrapper for the community-contribution tools.

WHY THIS EXISTS
----------------
The three community-contribution tools (submit_contribution,
review_contributions, torture_test) previously used the ``requests`` library
with a ``GITHUB_TOKEN`` read from ``.env``. That required every user to
manually create and paste a personal access token — a barrier that meant
almost nobody contributed.

The ``gh`` CLI (installed by the fork-based installer) handles auth via
``gh auth login`` (a browser flow) and stores credentials in the OS keychain.
This module wraps ``gh`` so the tools use the same auth with zero token
management. ``gh api`` maps 1:1 to the GitHub REST API, so the refactor is
mechanical: ``requests.get(url, headers=...)`` becomes ``gh_api("GET", path)``.

USAGE
-----
    from gh_client import gh_api, gh, gh_available

    if not gh_available():
        return {"error": "gh CLI not found. Run 'gh auth login' first."}

    data = gh_api("GET", "repos/Ziggibot0/vaultbot")
    gh(["pr", "list", "--state", "open"])
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# Add the backend dir to sys.path so we can import subprocess_utils (which
# hides console windows on Windows). This mirrors the pattern used by the
# existing custom tools.
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from subprocess_utils import run as _subprocess_run  # noqa: E402


class GhError(Exception):
    """Raised when a ``gh`` command fails or returns a non-zero exit code."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def gh_available() -> bool:
    """Return True if the ``gh`` CLI is on PATH and authenticated."""
    try:
        r = _subprocess_run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def gh(
    args: list[str], cwd: str | None = None, timeout: int = 60
) -> subprocess.CompletedProcess:
    """Run an arbitrary ``gh`` subcommand and return the CompletedProcess.

    Raises GhError on non-zero exit. ``args`` is the list of tokens AFTER
    ``gh`` (e.g. ``["pr", "list"]``).
    """
    try:
        r = _subprocess_run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except FileNotFoundError as err:
        raise GhError(
            "gh CLI not found. Install it from https://cli.github.com and "
            "run 'gh auth login'."
        ) from err
    except subprocess.TimeoutExpired as err:
        raise GhError(f"gh command timed out: {' '.join(args)}") from err

    if r.returncode != 0:
        raise GhError(
            f"gh {' '.join(args)} failed (exit {r.returncode}): "
            f"{r.stderr.strip()[:500]}",
            stderr=r.stderr,
        )
    return r


def gh_api(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 60,
) -> dict | list:
    """Call the GitHub REST API via ``gh api`` and return parsed JSON.

    ``method`` is GET/POST/PUT/PATCH/DELETE. ``path`` is the API path without
    the leading slash (e.g. ``repos/Ziggibot0/vaultbot``). ``body``, if
    given, is serialized to JSON and sent as the request body (via stdin).

    Raises GhError on failure. Returns the parsed JSON (dict or list).
    """
    cmd = ["api", "-X", method, path]
    if body is not None:
        cmd += ["--input", "-"]

    try:
        r = _subprocess_run(
            ["gh", *cmd],
            input=json.dumps(body) if body is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as err:
        raise GhError(
            "gh CLI not found. Install it from https://cli.github.com and "
            "run 'gh auth login'."
        ) from err
    except subprocess.TimeoutExpired as err:
        raise GhError(f"gh api {method} {path} timed out") from err

    if r.returncode != 0:
        raise GhError(
            f"gh api {method} {path} failed (exit {r.returncode}): "
            f"{r.stderr.strip()[:500]}",
            stderr=r.stderr,
        )

    out = r.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Some endpoints return non-JSON (e.g. empty body). Return raw text.
        return {"_raw": out}


def gh_raw(owner: str, repo: str, ref: str, path: str, timeout: int = 30) -> str:
    """Fetch the raw content of a file at a given ref via the contents API.

    Uses the ``application/vnd.github.raw`` media type so ``gh api`` returns
    the file bytes directly (not base64). Returns the decoded text.
    """
    cmd = [
        "api",
        "-H",
        "Accept: application/vnd.github.raw",
        f"repos/{owner}/{repo}/contents/{path}?ref={ref}",
    ]
    try:
        r = _subprocess_run(
            ["gh", *cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as err:
        raise GhError(
            "gh CLI not found. Install it from https://cli.github.com and "
            "run 'gh auth login'."
        ) from err
    except subprocess.TimeoutExpired as err:
        raise GhError(f"gh raw {owner}/{repo}/{path}@{ref} timed out") from err

    if r.returncode != 0:
        raise GhError(
            f"gh raw {owner}/{repo}/{path}@{ref} failed (exit {r.returncode}): "
            f"{r.stderr.strip()[:500]}",
            stderr=r.stderr,
        )
    return r.stdout
