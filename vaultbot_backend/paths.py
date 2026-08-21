"""Central path resolution for the VaultBot framework.

VaultBot has TWO roots, and they are no longer the same directory:

* ``FRAMEWORK_ROOT`` — the directory that contains ``vaultbot_backend/``
  (the git repo).  Holds framework content: ``System/``, ``Knowledge/``
  (Textbooks/Concepts/Architecture), ``baseline/``, ``learningMaterial/``,
  ``.env``, ``.venv/``.  This is the program, not the user's mind.

* ``VAULT_ROOT`` — the user's Obsidian vault.  In the installed layout this
  is ``FRAMEWORK_ROOT/Vault/`` (the user opens *this* folder in Obsidian).
  In the flattened dev layout it is the repo root (where ``.obsidian/``
  lives).  Holds user content: ``User/``, ``Memory/``, ``Knowledge/Research/``.

The two roots are separated so the user's Obsidian file explorer shows only
their notes — never the framework internals.
"""

from pathlib import Path

# The framework root is the directory that contains vaultbot_backend/.
# paths.py lives in vaultbot_backend/, so parent.parent is the framework root.
FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent


def _resolve_vault_root() -> Path:
    """Locate the user's vault root.

    Installed layout: ``FRAMEWORK_ROOT/Vault/`` (has ``.obsidian/``).
    Dev (flattened) layout: the repo root (has ``.obsidian/``).
    Falls back to ``FRAMEWORK_ROOT/Vault/`` if it exists, else the framework
    root itself (covers CI, where ``VAULT_PATH`` is set explicitly and
    ``.obsidian/`` may not exist).
    """
    # Installed layout: the vault is a Vault/ subfolder of the framework.
    candidate = FRAMEWORK_ROOT / "Vault"
    if (candidate / ".obsidian").is_dir():
        return candidate

    # Dev / legacy: walk up from the framework root to find .obsidian/.
    current = FRAMEWORK_ROOT
    for _ in range(5):
        if (current / ".obsidian").is_dir():
            return current
        parent = current.parent
        if parent == current:  # filesystem root
            break
        current = parent

    # Fallback: the Vault/ subfolder if it exists, else the framework root.
    return candidate if candidate.is_dir() else FRAMEWORK_ROOT


VAULT_ROOT = _resolve_vault_root()


def content_roots() -> list[Path]:
    """Roots to scan for vault content (user vault + framework content).

    The user's notes live under ``VAULT_ROOT``; framework content
    (``System/Procedures/``, ``Knowledge/Concepts/``, ``baseline/``) lives
    under ``FRAMEWORK_ROOT``.  Retrieval (indexer, graph, procedure tracker)
    must see BOTH so procedures and baseline knowledge stay retrievable even
    though they sit outside the user's vault.  In the flattened dev layout
    the two roots are the same directory, so this returns a single entry.
    """
    roots = [VAULT_ROOT]
    if FRAMEWORK_ROOT != VAULT_ROOT:
        roots.append(FRAMEWORK_ROOT)
    return roots


# Logical paths that resolve against the FRAMEWORK root (not the vault).
# These are framework-owned content: procedures, directives, baseline
# knowledge, textbooks, architecture maps.  Everything else (User/, Memory/,
# Knowledge/Research/) is user content and resolves against VAULT_ROOT.
_FRAMEWORK_PREFIXES = (
    "System/",
    "baseline/",
    "Knowledge/Concepts/",
    "Knowledge/Textbooks/",
    "Knowledge/Architecture/",
    "Knowledge/Procedures/",
    "Knowledge/Tools/",
    "Knowledge/Simulations/",
    "Knowledge/Biology/",
)


def resolve_content_path(file_path: str | Path) -> Path:
    """Resolve a logical vault-relative path to its physical location.

    Framework-owned content (``System/``, ``baseline/``, framework
    ``Knowledge/`` subfolders) resolves against ``FRAMEWORK_ROOT``; user
    content (``User/``, ``Memory/``, ``Knowledge/Research/``) resolves
    against ``VAULT_ROOT``.  In the flattened dev layout the two roots are
    identical, so this is a no-op there.
    """
    p = Path(file_path)
    norm = str(p).replace("\\", "/").lstrip("./")
    if norm.startswith(_FRAMEWORK_PREFIXES):
        return (FRAMEWORK_ROOT / p).resolve()
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
