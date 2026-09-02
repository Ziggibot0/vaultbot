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

    from upstream_identity import resolve_upstream
    owner, repo = resolve_upstream()
    data = gh_api("GET", f"repos/{owner}/{repo}")
    gh(["pr", "list", "--state", "open"])
"""

from __future__ import annotations

import json
import os
import subprocess
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def get_instance_id() -> str:
    """Return this VaultBot instance's stable ID, or "" if unavailable.

    The instance ID is the *identity* of this VaultBot instance — stable
    across restarts, model swaps, and GitHub accounts. It is NOT the GitHub
    account: one account can drive many instances (laptop + desktop, multiple
    vaults), and one instance can be driven by whichever account is authed at
    push time. The account is a *credential* (a transport for pushing); the
    instance ID is the *thing*.

    The ID is generated once by ``identity.py`` and stored in
    ``vaultbot_backend/identity/INSTANCE_ID`` (gitignored, so it never leaks
    into a PR). This helper reads it directly so the contribution tools can
    attribute a PR/issue to the instance without needing the full Identity
    singleton.
    """
    try:
        identity_dir = os.path.join(_backend_dir, "identity")
        instance_id_path = os.path.join(identity_dir, "INSTANCE_ID")
        with open(instance_id_path, encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, FileNotFoundError):
        return ""


def _bot_env() -> dict[str, str] | None:
    """Return an env dict that makes ``gh`` act as the bot account, or None.

    When ``VAULTBOT_GH_BOT_USER`` is set (e.g. "ziggibot-uni"), retrieve that
    account's token from the keyring via ``gh auth token --user`` and return
    an env with ``GH_TOKEN`` set to it. This makes the vaultbot author PRs as
    the bot account so the human operator (the code owner) can approve them —
    GitHub does not allow approving your own PR, so the bot and the human must
    be different accounts.

    Returns None when the env var is unset or the token can't be retrieved, so
    callers fall back to the active account (unchanged behavior). The token is
    a scoped ``gho_`` OAuth token already stored in the OS keychain by
    ``gh auth login`` — this only surfaces it to the ``gh`` subprocess, exactly
    as ``gh`` itself does internally.
    """
    bot_user = os.environ.get("VAULTBOT_GH_BOT_USER", "").strip()
    if not bot_user:
        return None
    try:
        r = _subprocess_run(
            ["gh", "auth", "token", "--user", bot_user],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    token = r.stdout.strip()
    if r.returncode != 0 or not token:
        return None
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    return env


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
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=_bot_env(),
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_bot_env(),
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_bot_env(),
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
