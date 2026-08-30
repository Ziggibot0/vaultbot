"""upstream_identity.py — single source of truth for "which repo am I?".

WHY THIS EXISTS
----------------
The community-contribution tools (submit_contribution, review_contributions,
torture_test, github_issues) and the List-GitHub-Issues procedure all need to
know the upstream GitHub repo they operate on.  Previously each tool
hardcoded a different default (``ziggibot-uni``, ``Ziggibot0``, ``seakel``)
and silently fell back to it when git-remote detection failed.  That
splintering caused real bugs: a 404 because the fallback repo didn't exist,
cross-tool disagreement about where to file PRs, and procedures that could
never succeed because they pointed at the wrong owner.

This module is the **one place** that answers "what is my upstream?".
Resolution order:

1. **Env vars** — ``UPSTREAM_OWNER`` + ``UPSTREAM_REPO`` in ``.env``.
   Explicit, operator-controlled, overrides everything.  This is the
   recommended way to configure a fork whose ``origin`` differs from the
   canonical upstream (e.g. a personal fork that contributes back to the
   parent project).

2. **git remote** — ``git remote get-url upstream`` (preferred) or
   ``git remote get-url origin`` (fallback), parsed from the nearest
   ``.git`` directory found by walking up from the backend dir.  A fork
   typically has two remotes: ``origin`` (the fork, where you push) and
   ``upstream`` (the canonical project, where you file PRs).  Preferring
   ``upstream`` ensures contribution tools target the right repo.  If
   there's no ``upstream`` remote (the vault root *is* the canonical repo),
   ``origin`` is used.

3. **No silent fallback.**  If neither source yields an answer the function
   raises ``UpstreamIdentityError`` with a clear message telling the
   operator exactly what to set.  A loud failure is always better than a
   wrong default that 404s three tools later.

USAGE
-----
    from upstream_identity import resolve_upstream

    try:
        owner, repo = resolve_upstream()
    except UpstreamIdentityError as e:
        return {"error": str(e)}

This replaces every ``upstream_owner = "Ziggibot0"`` / ``"ziggibot-uni"`` /
``"seakel"`` hardcode and the ad-hoc ``subprocess.run(["git", "remote", ...])``
blocks duplicated across the four tools.
"""

from __future__ import annotations

import os
import re
import subprocess


class UpstreamIdentityError(Exception):
    """Raised when the upstream repo cannot be determined.

    The message is operator-actionable: it says what to set and where.
    """


def _find_git_root(start: str) -> str | None:
    """Walk up from ``start`` looking for a directory containing ``.git``.

    Returns the first directory that has a ``.git`` entry, or ``None`` if
    none is found before the filesystem root.  ``start`` is the backend
    directory (``vaultbot_backend/``); the git root is typically 2-3 levels
    above it.
    """
    current = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:  # filesystem root
            return None
        current = parent


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract ``(owner, repo)`` from a GitHub remote URL.

    Handles both HTTPS (``https://github.com/owner/repo.git``) and SSH
    (``git@github.com:owner/repo.git``) forms.  Returns ``None`` if the URL
    isn't a GitHub URL or doesn't contain an owner/repo pair.
    """
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url.strip())
    if match:
        return match.group(1), match.group(2)
    return None


def resolve_upstream(backend_dir: str | None = None) -> tuple[str, str]:
    """Return ``(owner, repo)`` for this VaultBot's upstream GitHub repo.

    Resolution order: env vars → git remote → raise.

    Args:
        backend_dir: The ``vaultbot_backend/`` directory.  If omitted, the
            parent of this file's directory is used (correct for tools in
            ``custom_tools/``).

    Raises:
        UpstreamIdentityError: If neither env vars nor git remote yield a
            valid owner/repo.  The error message tells the operator exactly
            what to add to ``.env``.
    """
    # An explicitly selected development workspace is authoritative for all
    # project operations. Its local root and GitHub identity were validated
    # together when selected, preventing filesystem/GitHub split-brain.
    from workspace import WorkspaceError, workspace_registry

    try:
        selected = workspace_registry.get()
    except WorkspaceError as exc:
        raise UpstreamIdentityError(str(exc)) from exc
    if selected is not None:
        return selected.owner, selected.repository

    # ── 1. Env vars (legacy configuration when no workspace is selected) ─
    env_owner = os.environ.get("UPSTREAM_OWNER", "").strip()
    env_repo = os.environ.get("UPSTREAM_REPO", "").strip()
    if env_owner and env_repo:
        return env_owner, env_repo
    # Partial env config is a misconfiguration — surface it loudly rather
    # than silently mixing env + git-remote.
    if env_owner or env_repo:
        raise UpstreamIdentityError(
            "Partial upstream config in .env: UPSTREAM_OWNER="
            f"{env_owner!r} UPSTREAM_REPO={env_repo!r}. "
            "Set BOTH (or neither, to fall back to git remote)."
        )

    # ── 2. git remote — prefer 'upstream', fall back to 'origin' ────────
    # A fork has two remotes: 'origin' (the fork, where you push) and
    # 'upstream' (the canonical project, where you file PRs).  The
    # contribution tools need the *upstream* remote — that's the repo to
    # open PRs against.  If there's no 'upstream' remote (the vault root
    # IS the canonical repo), 'origin' is correct.
    if backend_dir is None:
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git_root = _find_git_root(backend_dir)
    if git_root is not None:
        for remote_name in ("upstream", "origin"):
            try:
                r = subprocess.run(
                    ["git", "remote", "get-url", remote_name],
                    capture_output=True,
                    text=True,
                    cwd=git_root,
                    timeout=10,
                )
                if r.returncode == 0:
                    parsed = _parse_github_url(r.stdout)
                    if parsed is not None:
                        return parsed
            except (subprocess.TimeoutExpired, OSError):
                pass  # git not available or hung — try next remote

    # ── 3. No silent fallback — raise an actionable error ───────────────
    raise UpstreamIdentityError(
        "Could not determine the upstream GitHub repo. "
        "Set UPSTREAM_OWNER and UPSTREAM_REPO in .env, e.g.:\n"
        "  UPSTREAM_OWNER=Ziggibot0\n"
        "  UPSTREAM_REPO=vaultbot\n"
        "Or ensure the vault root is a git repo with a GitHub 'upstream' "
        "or 'origin' remote (git remote get-url upstream)."
    )
