"""Regression test: installer in-repo detection + fixed vault name.

Guards against two classes of bugs:

1. **In-repo detection failure.** When the installer was run inside an
   existing clone (or on case-insensitive filesystems where ``VaultBot``
   matches ``vaultbot``), the ``Test-Path``/``-d`` guard used the
   current directory as the framework path without realizing it. The
   fix detects this by checking for ``vaultbot_backend/`` + ``setup.ps1``
   (or ``setup.sh``) in ``$PWD`` and in the candidate framework folder.

2. **Vault folder rename breaking updates.** The installer previously
   asked users to name their vault folder and then ``git mv``-ed the
   shipped ``vault/`` to the chosen name. This broke ``git pull``
   updates: upstream changes to ``vaultbot-stuff/System/Procedures/``
   etc. landed in ``vault/`` (the old name) while the user's vault lived
   elsewhere, so nobody got procedure updates. The fix REMOVES the
   rename entirely — the vault folder name is FIXED to ``myvault`` so
   upstream updates always merge into the right place for every user.

This is a source-level guard, not a runtime test: PowerShell/bash aren't
available in the Linux CI runner, so we assert on the script text itself.
It catches the exact regression class without needing a Windows host.

Run: pytest tests/test_installer_vault_rename_regression.py -v
"""

from __future__ import annotations

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


# ── Fixed vault name (no rename) ──────────────────────────────────────────


def test_ps1_uses_fixed_vault_name(setup_ps1_text: str) -> None:
    """setup.ps1 must use a FIXED vault folder name ("myvault"), not ask the user.

    Allowing users to rename the vault folder broke `git pull` updates:
    upstream changes to vaultbot-stuff/System/Procedures/ etc. landed in
    ``vault/`` (the old name) while the user's vault lived elsewhere.
    The vault folder name is now FIXED to ``myvault`` so updates always
    merge into the right place for every user.
    """
    assert '"myvault"' in setup_ps1_text or "'myvault'" in setup_ps1_text, (
        "setup.ps1 no longer uses a fixed 'myvault' vault name — the "
        "update-safe vault name guard regressed."
    )
    # Must NOT ask the user to name the vault (that was the old broken flow).
    assert "What should your vault be called" not in setup_ps1_text, (
        "setup.ps1 is asking the user to name the vault again — this "
        "breaks git pull updates for renamed vaults."
    )
    # Must NOT use git mv to rename vault/ (the rename is gone entirely).
    assert "git mv vault" not in setup_ps1_text, (
        "setup.ps1 still uses `git mv vault` — the vault rename logic "
        "should have been removed entirely."
    )


def test_sh_uses_fixed_vault_name(setup_sh_text: str) -> None:
    """setup.sh must use a FIXED vault folder name ("myvault"), not ask the user."""
    assert '"myvault"' in setup_sh_text or "'myvault'" in setup_sh_text, (
        "setup.sh no longer uses a fixed 'myvault' vault name — the "
        "update-safe vault name guard regressed."
    )
    # Must NOT ask the user to name the vault (that was the old broken flow).
    assert "What should your vault be called" not in setup_sh_text, (
        "setup.sh is asking the user to name the vault again — this "
        "breaks git pull updates for renamed vaults."
    )
    # Must NOT use git mv to rename vault/ (the rename is gone entirely).
    assert "git mv vault" not in setup_sh_text, (
        "setup.sh still uses `git mv vault` — the vault rename logic "
        "should have been removed entirely."
    )
