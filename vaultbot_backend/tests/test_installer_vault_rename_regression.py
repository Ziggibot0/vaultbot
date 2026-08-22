"""Regression test: installer in-repo detection + git-aware vault rename.

Guards against the two bugs that caused a duplicate ``vault/`` folder
alongside the user-renamed vault (e.g. ``myvault/``):

1. **In-repo detection failure.** When the installer was run inside an
   existing clone (or on case-insensitive filesystems where ``VaultBot``
   matches ``vaultbot``), the ``Test-Path``/``-d`` guard used the
   current directory as the framework path without realizing it. The
   fix detects this by checking for ``vaultbot_backend/`` + ``setup.ps1``
   (or ``setup.sh``) in ``$PWD`` and in the candidate framework folder.

2. **Git-unaware vault rename.** ``Rename-Item`` / ``mv`` on the
   git-tracked ``vault/`` folder didn't tell git about the rename, so
   ``git checkout`` restored ``vault/`` with all 282 tracked files —
   creating a duplicate empty-ish ``vault/`` alongside the renamed
   vault. The fix uses ``git mv`` + a ``.gitignore`` path-prefix update
   + a commit when inside a git repo, so the rename persists across
   checkouts.

This is a source-level guard, not a runtime test: PowerShell/bash aren't
available in the Linux CI runner, so we assert on the script text itself.
It catches the exact regression class without needing a Windows host.

Run: pytest tests/test_installer_vault_rename_regression.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Both installers live at the repo root; this file is at
# vaultbot_backend/tests/test_installer_vault_rename_regression.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETUP_PS1 = _REPO_ROOT / "setup.ps1"
_SETUP_SH = _REPO_ROOT / "setup.sh"


@pytest.fixture(scope="module")
def setup_ps1_text() -> str:
    if not _SETUP_PS1.exists():
        pytest.skip(f"setup.ps1 not found at {_SETUP_PS1}")
    return _SETUP_PS1.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def setup_sh_text() -> str:
    if not _SETUP_SH.exists():
        pytest.skip(f"setup.sh not found at {_SETUP_SH}")
    return _SETUP_SH.read_text(encoding="utf-8-sig")


# ── In-repo detection ─────────────────────────────────────────────────────


def test_ps1_detects_run_inside_existing_repo(setup_ps1_text: str) -> None:
    """setup.ps1 must detect when $PWD itself is a VaultBot repo.

    Without this, the installer's ``Test-Path $frameworkPath`` guard is
    defeated by Windows case-insensitivity (``VaultBot`` ≡ ``vaultbot``)
    and silently uses the current directory as the framework path.
    """
    assert "vaultbot_backend" in setup_ps1_text and "setup.ps1" in setup_ps1_text, (
        "setup.ps1 no longer checks for vaultbot_backend/ + setup.ps1 in $PWD "
        "— the in-repo detection guard regressed."
    )
    assert "Already inside a VaultBot repo" in setup_ps1_text, (
        "setup.ps1 no longer warns when running inside an existing repo."
    )


def test_ps1_aborts_on_non_vaultbot_folder(setup_ps1_text: str) -> None:
    """setup.ps1 must abort if $frameworkPath exists but isn't a VaultBot repo.

    Without this, the installer would silently use an unrelated folder
    and clobber it.
    """
    assert "isn't a VaultBot repo" in setup_ps1_text, (
        "setup.ps1 no longer aborts when $frameworkPath exists but isn't a "
        "VaultBot repo — the safety guard regressed."
    )


def test_sh_detects_run_inside_existing_repo(setup_sh_text: str) -> None:
    """setup.sh must detect when $PWD itself is a VaultBot repo."""
    assert "vaultbot_backend" in setup_sh_text and "setup.sh" in setup_sh_text, (
        "setup.sh no longer checks for vaultbot_backend/ + setup.sh in $PWD "
        "— the in-repo detection guard regressed."
    )
    assert "Already inside a VaultBot repo" in setup_sh_text, (
        "setup.sh no longer warns when running inside an existing repo."
    )


def test_sh_aborts_on_non_vaultbot_folder(setup_sh_text: str) -> None:
    """setup.sh must abort if $FRAMEWORK_PATH exists but isn't a VaultBot repo."""
    assert "isn't a VaultBot repo" in setup_sh_text, (
        "setup.sh no longer aborts when $FRAMEWORK_PATH exists but isn't a "
        "VaultBot repo — the safety guard regressed."
    )


# ── Git-aware vault rename ────────────────────────────────────────────────


def test_ps1_uses_git_mv_for_vault_rename(setup_ps1_text: str) -> None:
    """setup.ps1 must use `git mv` to rename vault/ when inside a git repo.

    A plain ``Rename-Item`` doesn't tell git about the rename, so
    ``git checkout`` restores ``vault/`` — creating a duplicate folder
    alongside the renamed vault.
    """
    assert "git mv vault" in setup_ps1_text, (
        "setup.ps1 no longer uses `git mv vault` for the vault rename — "
        "the git-aware rename guard regressed (would cause duplicate vault/ "
        "on next checkout)."
    )


def test_ps1_updates_gitignore_on_rename(setup_ps1_text: str) -> None:
    """setup.ps1 must update .gitignore path prefixes when renaming vault/."""
    # The regex replacement changes vault/ → <new-name>/ in .gitignore rules.
    assert "vault/" in setup_ps1_text and "gitignore" in setup_ps1_text.lower(), (
        "setup.ps1 no longer updates .gitignore when renaming vault/ — "
        "the gitignore path-prefix update regressed."
    )
    # Must commit the rename so it persists across checkouts.
    assert re.search(r"git.*commit.*rename vault", setup_ps1_text, re.IGNORECASE), (
        "setup.ps1 no longer commits the vault rename — the rename won't "
        "persist across git checkouts, causing a duplicate vault/."
    )


def test_ps1_skips_rename_for_default_name(setup_ps1_text: str) -> None:
    """setup.ps1 must skip the rename when the user chose the default 'vault'."""
    assert '$chosenVault -eq "vault"' in setup_ps1_text, (
        "setup.ps1 no longer skips the rename when the user chose the "
        "default name 'vault' — this would cause an unnecessary git mv."
    )


def test_sh_uses_git_mv_for_vault_rename(setup_sh_text: str) -> None:
    """setup.sh must use `git mv` to rename vault/ when inside a git repo."""
    assert "git mv vault" in setup_sh_text, (
        "setup.sh no longer uses `git mv vault` for the vault rename — "
        "the git-aware rename guard regressed (would cause duplicate vault/ "
        "on next checkout)."
    )


def test_sh_updates_gitignore_on_rename(setup_sh_text: str) -> None:
    """setup.sh must update .gitignore path prefixes when renaming vault/."""
    assert "gitignore" in setup_sh_text.lower(), (
        "setup.sh no longer updates .gitignore when renaming vault/ — "
        "the gitignore path-prefix update regressed."
    )
    assert re.search(
        r"git.*commit.*rename vault", setup_sh_text, re.IGNORECASE | re.DOTALL
    ), (
        "setup.sh no longer commits the vault rename — the rename won't "
        "persist across git checkouts, causing a duplicate vault/."
    )


def test_sh_skips_rename_for_default_name(setup_sh_text: str) -> None:
    """setup.sh must skip the rename when the user chose the default 'vault'."""
    assert '"vault"' in setup_sh_text and "no rename needed" in setup_sh_text.lower(), (
        "setup.sh no longer skips the rename when the user chose the "
        "default name 'vault' — this would cause an unnecessary git mv."
    )


def test_sh_git_mv_falls_back_to_mv(setup_sh_text: str) -> None:
    """setup.sh must fall back to plain `mv` if `git mv` fails (non-git repo)."""
    assert 'mv "$SHIPPED_VAULT" "$CHOSEN_VAULT"' in setup_sh_text, (
        "setup.sh no longer falls back to a filesystem `mv` when `git mv` "
        "fails — the non-git-repo fallback regressed."
    )
    # The fallback must exist in the else branch of the git mv check.
    assert "git mv failed" in setup_sh_text.lower(), (
        "setup.sh no longer documents the git mv fallback — the non-git "
        "fallback path regressed."
    )
