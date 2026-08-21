"""Central path resolution for the VaultBot framework.

VaultBot has TWO roots:

* ``VAULT_ROOT`` — the user's Obsidian vault.  This is the folder the user
  names during install and opens in Obsidian.  Holds ALL user-visible
  content: ``System/Procedures/``, ``Knowledge/``, ``baseline/``, ``Memory/``,
  ``User/``, ``.obsidian/``.  Procedures live HERE so they're visible in the
  Obsidian file explorer (transparency layer).

* ``FRAMEWORK_ROOT`` — the directory that contains ``vaultbot_backend/``.
  In the **installed** layout this is ``VAULT_ROOT/vaultbot/`` (a subfolder
  inside the vault, hidden from the Obsidian file explorer via ignore
  filters).  Holds framework plumbing: ``vaultbot_backend/``, ``.venv/``,
  ``.env``, ``setup.ps1``, ``learningMaterial/``.  In the **flattened dev**
  layout (this repo), ``FRAMEWORK_ROOT`` == ``VAULT_ROOT`` == the repo root.
"""

from pathlib import Path

# The framework root is the directory that contains vaultbot_backend/.
# paths.py lives in vaultbot_backend/, so parent.parent is the framework root.
FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent


def _resolve_vault_root() -> Path:
    """Locate the user's vault root.

    Installed layout: the vault is the PARENT of the framework root
    (``FRAMEWORK_ROOT`` == ``<vault>/vaultbot/``, so the vault is one level
    up).  The vault has ``.obsidian/`` and ``System/``.

    Dev (flattened) layout: the repo root has both ``vaultbot_backend/`` and
    ``.obsidian/`` / ``System/``, so ``FRAMEWORK_ROOT`` IS the vault root.

    CI: ``VAULT_PATH`` is set explicitly to the repo workspace; ``.obsidian/``
    may not exist.  Falls back to ``FRAMEWORK_ROOT``.
    """
    # Installed layout: framework lives in <vault>/vaultbot/, so the vault
    # is the parent of the framework root.  Only accept it if the framework
    # root itself is named "vaultbot" (the installed subfolder name) AND the
    # parent looks like a vault (has .obsidian/ or System/).
    candidate = FRAMEWORK_ROOT.parent
    if FRAMEWORK_ROOT.name == "vaultbot" and (
        (candidate / ".obsidian").is_dir() or (candidate / "System").is_dir()
    ):
        return candidate

    # Dev / legacy flattened: the framework root IS the vault root (it has
    # .obsidian/ or System/ directly).
    if (FRAMEWORK_ROOT / ".obsidian").is_dir() or (FRAMEWORK_ROOT / "System").is_dir():
        return FRAMEWORK_ROOT

    # Walk up to find a .obsidian/ (covers edge cases).
    current = FRAMEWORK_ROOT
    for _ in range(5):
        if (current / ".obsidian").is_dir():
            return current
        parent = current.parent
        if parent == current:  # filesystem root
            break
        current = parent

    # Fallback: the framework root itself (covers CI, where VAULT_PATH is
    # set explicitly and .obsidian/ may not exist).
    return FRAMEWORK_ROOT


VAULT_ROOT = _resolve_vault_root()


def content_roots() -> list[Path]:
    """Roots to scan for vault content (user vault + framework content).

    The user's notes AND framework content (procedures, knowledge, baseline)
    all live under ``VAULT_ROOT`` in the new layout — ``System/Procedures/``
    is visible in Obsidian.  ``FRAMEWORK_ROOT`` (``<vault>/vaultbot/``) holds
    only plumbing (``vaultbot_backend/``, ``.venv/``), which is not content.
    In the flattened dev layout the two roots are the same directory, so
    this returns a single entry.  In the installed layout the framework root
    is still included as a fallback in case any content drifts there.
    """
    roots = [VAULT_ROOT]
    if FRAMEWORK_ROOT != VAULT_ROOT:
        roots.append(FRAMEWORK_ROOT)
    return roots


# In the new layout, ALL content (System/, Knowledge/, baseline/) lives in
# the vault root and is visible in Obsidian.  The framework root
# (<vault>/vaultbot/) holds only plumbing (vaultbot_backend/, .venv/).
# resolve_content_path routes everything to VAULT_ROOT.  The framework
# prefixes list is kept empty for backward compatibility — if any content
# somehow lives under the framework root in a legacy install, the
# content_roots() fallback scan still finds it.
_FRAMEWORK_PREFIXES: tuple[str, ...] = ()


def resolve_content_path(file_path: str | Path) -> Path:
    """Resolve a logical vault-relative path to its physical location.

    All content (``System/``, ``Knowledge/``, ``baseline/``, ``User/``,
    ``Memory/``) resolves against ``VAULT_ROOT`` — the user's Obsidian vault.
    In the flattened dev layout ``VAULT_ROOT`` == ``FRAMEWORK_ROOT``, so
    this is a no-op there.
    """
    p = Path(file_path)
    return (VAULT_ROOT / p).resolve()


def is_within_content_roots(path: str | Path) -> bool:
    """True if ``path`` resolves inside VAULT_ROOT or FRAMEWORK_ROOT.

    Used by the write tools as the path-traversal guard: a write is allowed
    only if it lands inside one of the two content roots.
    """
    resolved = Path(path).resolve()
    for root in (VAULT_ROOT, FRAMEWORK_ROOT):
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False
