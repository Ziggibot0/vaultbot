"""Regression test: the plugin self-updater's git path must LAND on the
release with ``reset --hard`` — never ``merge``.

Every install is ``git clone --branch <tag> --depth 1`` (setup.ps1 /
setup.sh), i.e. a SHALLOW, detached-HEAD clone. The old updater tried to
``git merge --ff-only FETCH_HEAD`` (falling back to ``git merge
FETCH_HEAD``). On a shallow clone the fetched tag shares no history with
local HEAD, so ``--ff-only`` always aborts ("Not possible to
fast-forward") and the fallback merge dies on "refusing to merge unrelated
histories" — which left EVERY existing user permanently unable to update.

The fix: fetch the tag and ``git reset --hard FETCH_HEAD``. That needs only
the target commit's tree (no common ancestor), so it can never conflict and
is idempotent. ``reset --hard`` never touches untracked files, so user data
and bot-authored procedures survive, and all runtime-state/log files are
gitignored.

Source-level guard (Electron/Obsidian isn't available in CI), same pattern
as test_updater_archive_paths.py.

Run: pytest tests/test_updater_shallow_reset.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_JS = _REPO_ROOT / "myvault" / ".obsidian" / "plugins" / "vaultbot" / "main.js"


@pytest.fixture(scope="module")
def main_js_text() -> str:
    if not _MAIN_JS.exists():
        pytest.skip(f"plugin main.js not found at {_MAIN_JS}")
    return _MAIN_JS.read_text(encoding="utf-8")


def _git_update_block(text: str) -> str:
    """Return the source of the git-based update branch (the block guarded
    by ``if (fs.existsSync(gitDir))``) up to the tarball fallback."""
    start = text.find("if (fs.existsSync(gitDir))")
    assert start != -1, "performSelfUpdate has no git-based update branch."
    # The tarball path begins with this notify; the git block ends before it.
    end = text.find("Downloading update from GitHub", start)
    assert end != -1, "Could not locate the end of the git update block."
    return text[start:end]


def test_git_update_uses_reset_hard(main_js_text: str) -> None:
    """The git path must land on the release with ``reset --hard
    FETCH_HEAD`` — the whole point of the shallow-clone fix."""
    block = _git_update_block(main_js_text)
    assert re.search(r"reset['\"],\s*['\"]--hard['\"],\s*['\"]FETCH_HEAD", block), (
        "performSelfUpdate's git path no longer resets --hard onto the "
        "fetched release. On a shallow clone this is the ONLY operation "
        "that works — reverting to merge re-breaks updates for every user."
    )


def test_git_update_does_not_merge(main_js_text: str) -> None:
    """The git path must NOT merge — merge is unrecoverable on a shallow,
    detached clone and is exactly what left existing users stuck."""
    block = _git_update_block(main_js_text)
    assert "--ff-only" not in block, (
        "performSelfUpdate's git path uses 'merge --ff-only', which ALWAYS "
        "aborts on a shallow clone ('Not possible to fast-forward')."
    )
    assert not re.search(r"merge['\"],\s*['\"]FETCH_HEAD", block), (
        "performSelfUpdate's git path merges FETCH_HEAD, which dies on "
        "'refusing to merge unrelated histories' on a shallow clone."
    )


def test_git_update_backs_up_modified_tracked_files(main_js_text: str) -> None:
    """Before reset --hard discards tracked local edits, they must be
    backed up (belt-and-braces, so nothing is ever silently lost)."""
    block = _git_update_block(main_js_text)
    assert "diff" in block and "--name-only" in block, (
        "performSelfUpdate's git path does not enumerate locally-modified "
        "tracked files before reset --hard — a bot/user edit to a tracked "
        "file would be silently lost."
    )
    assert ".vaultbot-update-backup" in block, (
        "performSelfUpdate's git path does not back up modified tracked "
        "files to .vaultbot-update-backup/ before resetting."
    )


@pytest.mark.parametrize("installer", ["setup.ps1", "setup.sh"])
def test_installer_repairs_existing_install_with_reset(installer: str) -> None:
    """Re-running an installer over an existing install must repair it to
    the latest release via fetch + reset --hard (the rescue path for users
    already stuck on the old merge-based updater), never via merge."""
    path = _REPO_ROOT / installer
    if not path.exists():
        pytest.skip(f"{installer} not found")
    text = path.read_text(encoding="utf-8")
    assert "reset --hard FETCH_HEAD" in text, (
        f"{installer} does not repair an existing install with "
        "'reset --hard FETCH_HEAD' — stuck users can't be rescued by "
        "re-running the installer."
    )
    assert "fetch --depth 1" in text, (
        f"{installer} does not fetch the target release tag before reset."
    )
