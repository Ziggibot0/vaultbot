"""Central path resolution for the VaultBot framework.

VaultBot has TWO roots in the new layout:

* ``FRAMEWORK_ROOT`` — the directory that contains ``vaultbot_backend/``.
  This is the repo root (or the install folder).  Holds the thin engine
  (``vaultbot_backend/``), ``.env``, ``.venv/``, ``setup.ps1``, and the
  ``myvault/`` subfolder.

* ``VAULT_ROOT`` — the user's Obsidian vault.  By default this is
  ``FRAMEWORK_ROOT/myvault``.  The vault folder name is FIXED to
  ``myvault`` so upstream updates (new procedures, Knowledge notes, plugin
  code) always merge into the right place for every user.  Holds the
  user's notes plus the transparent ``vaultbot-stuff/`` folder (procedures,
  knowledge, baseline, memory).

The vault is a SIBLING of the backend, not an ancestor.  Code that needs the
vault root must NOT walk up from ``__file__`` — it must resolve against
``FRAMEWORK_ROOT`` (or read ``VAULT_PATH``).  This module is the single
source of truth for both roots.
"""

from __future__ import annotations

import os
from pathlib import Path

# vaultbot_backend/paths.py -> parent = vaultbot_backend, parent.parent = the
# framework root (repo root / install folder).
FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent

# The default vault subfolder name.  The vault folder name is FIXED to
# "myvault" so that upstream updates always merge into the right place.
DEFAULT_VAULT_DIR = "myvault"

# Content that lives inside the vault under vaultbot-stuff/ (the transparent
# brain).  resolve_content_path maps these logical prefixes to
# VAULT_ROOT/vaultbot-stuff/<path>.
VAULTBOT_STUFF_DIR = "vaultbot-stuff"
_CONTENT_PREFIXES: tuple[str, ...] = (
    "System",
    "Knowledge",
    "baseline",
    "Memory",
)


def _resolve_vault_root() -> Path:
    """Locate the user's vault root.

    Precedence:
    1. ``VAULT_PATH`` env (absolute, or relative to FRAMEWORK_ROOT).
    2. ``FRAMEWORK_ROOT/myvault`` if it exists.
    3. ``FRAMEWORK_ROOT`` (dev/CI fallback where the vault is flattened).
    """
    vp = os.environ.get("VAULT_PATH", "")
    if vp:
        p = Path(vp)
        if not p.is_absolute():
            p = FRAMEWORK_ROOT / p
        return p.resolve()

    default = FRAMEWORK_ROOT / DEFAULT_VAULT_DIR
    if default.is_dir():
        return default.resolve()

    return FRAMEWORK_ROOT


def __getattr__(name: str):
    """Lazily resolve VAULT_ROOT on first access.

    ``main.py`` sets ``os.environ["VAULT_PATH"]`` to an absolute path AFTER
    its top-level imports run (it loads .env, then absolutizes VAULT_PATH).
    If VAULT_ROOT were computed at import time, it would read a stale/empty
    env.  Resolving it lazily — on first attribute access, which happens when
    a service actually uses it — guarantees it sees the finalized value.
    """
    if name == "VAULT_ROOT":
        return _resolve_vault_root()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def content_roots() -> list[Path]:
    """Roots to scan for vault content.

    All content (user notes + vaultbot-stuff/) lives under VAULT_ROOT.  The
    framework root holds only plumbing (vaultbot_backend/, .venv/), which is
    not content.  In a flattened dev layout the two roots coincide.
    """
    vault_root = _resolve_vault_root()
    roots = [vault_root]
    if vault_root != FRAMEWORK_ROOT:
        roots.append(FRAMEWORK_ROOT)
    return roots


def resolve_content_path(file_path: str | Path) -> Path:
    """Resolve a logical vault-relative path to its physical location.

    Content under ``System/``, ``Knowledge/``, ``baseline/``, or ``Memory/``
    lives inside ``VAULT_ROOT/vaultbot-stuff/``.  Everything else (user
    notes, ``.obsidian/``) lives directly under ``VAULT_ROOT``.
    """
    vault_root = _resolve_vault_root()
    p = Path(file_path)
    parts = p.parts
    if parts and parts[0] in _CONTENT_PREFIXES:
        return (vault_root / VAULTBOT_STUFF_DIR / p).resolve()
    return (vault_root / p).resolve()


def is_within_content_roots(path: str | Path) -> bool:
    """True if ``path`` resolves inside VAULT_ROOT or FRAMEWORK_ROOT.

    Used by the write tools as the path-traversal guard: a write is allowed
    only if it lands inside one of the two content roots.
    """
    resolved = Path(path).resolve()
    for root in (_resolve_vault_root(), FRAMEWORK_ROOT):
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False
