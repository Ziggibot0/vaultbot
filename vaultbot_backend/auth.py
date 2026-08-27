"""
auth.py — Shared-secret authentication for the VaultBot backend API.

WHY THIS EXISTS
---------------
The VaultBot backend listens on localhost:8000 with no authentication.
Any process on the same machine — a malicious npm package, a compromised
browser extension, a rogue script — can call POST /shutdown, POST
/custom_tools/call, or open a WebSocket to /ws and send arbitrary chat
messages or execute tools.

This module adds a shared-secret token that the Obsidian plugin and the
backend both know. The token is generated on first backend startup and
stored in a gitignored file. The plugin reads it from the same file and
sends it as a header on every request. The backend rejects any request
without a valid token.

DESIGN
------
- Token is a 64-char hex string (256 bits of entropy), generated via
  secrets.token_hex(32).
- Stored in vaultbot_backend/.vaultbot_auth_token (gitignored).
- The plugin reads the token file on startup and attaches it as the
  X-VaultBot-Token header on every fetch() and WebSocket connection.
- The backend middleware checks the header on every HTTP request and
  WebSocket upgrade. Missing/wrong token → 401.
- /health and /preflight are exempt (the plugin needs to check if the
  backend is alive before it can read the token file).
- The token file is created with restricted permissions (0o600 on POSIX).

Pure stdlib. No new dependencies.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
# Warn only once when auth bypass is enabled.
_auth_bypass_warned = False

# The token file lives next to this module (in vaultbot_backend/).
_TOKEN_FILE = Path(__file__).resolve().parent / ".vaultbot_auth_token"

# Endpoints that don't require auth (health checks, preflight for setup wizard).
_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/preflight",
        "/",
        "/callback",  # Google OAuth redirect — Google sends the code without
        # our auth token
    }
)

# Endpoints that ALWAYS require auth, even from localhost. These are the
# destructive/sensitive operations that a malicious local process could abuse.
# Everything else is trusted when the request comes from localhost (127.0.0.1
# or ::1) — the Obsidian plugin and local browser are trusted clients.
_AUTH_REQUIRED_PATHS: frozenset[str] = frozenset(
    {
        "/custom_tools/call",
        "/ws",
        "/shutdown",  # sendBeacon can't set custom headers; accept ?token=
        # query param instead
    }
)

# HTTP methods that mutate state. ANY mutating request requires auth, even
# from localhost. This closes the "dozens of mutating endpoints are open"
# hole (issue #230): a local process or DNS-rebinding/browser attack could
# otherwise POST /restart, /update/rollback, /config, /models/pull, etc. to
# restart the backend, roll back code, or reconfigure the LLM provider.
_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _generate_token() -> str:
    """Generate a new 64-char hex token (256 bits)."""
    return secrets.token_hex(32)


def get_or_create_token() -> str:
    """Read the existing token or create a new one.

    On first run, generates a token and writes it to the token file.
    On subsequent runs, reads the existing token.
    The file is created with restricted permissions where possible.
    """
    if _TOKEN_FILE.exists():
        try:
            token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
                return token
        except Exception:  # noqa: BLE001 — corrupt token file is non-fatal; regenerate below
            pass  # Corrupt file — regenerate below.

    # Generate a new token.
    token = _generate_token()

    # Write atomically: temp file + rename.
    tmp_path = _TOKEN_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(token, encoding="utf-8")
        # On POSIX, restrict permissions before renaming into place.
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.chmod(tmp_path, 0o600)
        tmp_path.replace(_TOKEN_FILE)
    except Exception:  # noqa: BLE001 — last-resort direct write if atomic write fails
        _TOKEN_FILE.write_text(token, encoding="utf-8")

    return token


def read_token() -> str:
    """Read the existing token WITHOUT creating one.

    Returns "" if the token file is missing or corrupt. Used by trusted
    internal callers (the MCP server, procedure subprocesses) that need to
    authenticate to the backend but must NOT create a token (only the backend
    itself creates the token on first boot).
    """
    if not _TOKEN_FILE.exists():
        return ""
    try:
        token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 — unreadable token file means no token available
        return ""
    if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
        return token
    return ""


def is_auth_exempt(path: str) -> bool:
    """Return True if the given path doesn't require authentication."""
    # Normalize: strip trailing slash, ensure leading slash.
    p = path.rstrip("/")
    if not p.startswith("/"):
        p = "/" + p
    return p in _AUTH_EXEMPT_PATHS


def is_auth_required(path: str) -> bool:
    """Return True if the given path ALWAYS requires authentication.

    Only destructive/sensitive endpoints require auth. Read/config endpoints
    are trusted from localhost — the backend only listens on 127.0.0.1.
    """
    # Normalize: strip trailing slash, ensure leading slash.
    p = path.rstrip("/")
    if not p.startswith("/"):
        p = "/" + p
    return p in _AUTH_REQUIRED_PATHS


def is_auth_required_for_method(path: str, method: str) -> bool:
    """Return True if this (path, method) requires authentication.

    Extends ``is_auth_required`` with a blanket rule: ANY mutating method
    (POST/PUT/DELETE/PATCH) requires the token, even from localhost. This
    closes issue #230 — every state-changing endpoint (restart, rollback,
    config, model pull, provider add, etc.) is now auth-gated. Read-only
    methods (GET/HEAD/OPTIONS) stay open so the plugin can read lists and
    health without a token.
    """
    if is_auth_required(path):
        return True
    return method.upper() in _MUTATING_METHODS


def validate_token(token: str | None) -> bool:
    """Check whether the given token matches the stored token.

    Constant-time comparison to prevent timing attacks (though on localhost
    this is defense-in-depth — an attacker who can measure timing on
    localhost already has bigger problems).

    In test/dev scenarios you can bypass auth by setting
    VAULTBOT_SKIP_AUTH=1. This differs from VAULTBOT_SKIP_LOCK which
    should only be used to bypass the PID lock. When the auth bypass is
    used, a single WARNING is emitted to make the bypass loud.
    """
    global _auth_bypass_warned
    if os.environ.get("VAULTBOT_SKIP_AUTH", "") == "1":
        # Warn once so CI/dev logs surface the bypass without spamming.
        if not _auth_bypass_warned:
            logger.warning(
                "VaultBot auth validation DISABLED via VAULTBOT_SKIP_AUTH=1 — "
                "this allows anonymous requests to mutating endpoints."
            )
            _auth_bypass_warned = True
        return True
    if token is None:
        return False
    try:
        expected = get_or_create_token()
    except Exception:  # noqa: BLE001 — token file read failure means auth is unavailable; fail closed
        return False
    # secrets.compare_digest is constant-time.
    return secrets.compare_digest(token, expected)
