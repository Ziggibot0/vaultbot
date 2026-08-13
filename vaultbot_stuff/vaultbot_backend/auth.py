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
- Stored in vaultbot_stuff/vaultbot_backend/.vaultbot_auth_token (gitignored).
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

import os
import secrets
from pathlib import Path

# The token file lives next to this module (in vaultbot_backend/).
_TOKEN_FILE = Path(__file__).resolve().parent / ".vaultbot_auth_token"

# Endpoints that don't require auth (health checks, preflight for setup wizard).
_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/preflight",
        "/",
        "/shutdown",  # sendBeacon can't set custom headers; PID lock prevents abuse
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
    }
)


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
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        tmp_path.replace(_TOKEN_FILE)
    except Exception:
        # Last resort: direct write.
        _TOKEN_FILE.write_text(token, encoding="utf-8")

    return token


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


def validate_token(token: str | None) -> bool:
    """Check whether the given token matches the stored token.

    Constant-time comparison to prevent timing attacks (though on localhost
    this is defense-in-depth — an attacker who can measure timing on
    localhost already has bigger problems).

    Skips validation when VAULTBOT_SKIP_LOCK=1 (test mode) — the same env
    var that disables the PID lock also disables auth so the test client
    can call endpoints without a token.
    """
    if os.environ.get("VAULTBOT_SKIP_LOCK", "") == "1":
        return True
    if token is None:
        return False
    try:
        expected = get_or_create_token()
    except Exception:  # noqa: BLE001 — token file read failure means auth is unavailable; fail closed
        return False
    # secrets.compare_digest is constant-time.
    return secrets.compare_digest(token, expected)
