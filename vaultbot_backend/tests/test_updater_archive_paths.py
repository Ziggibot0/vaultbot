"""Regression test: the plugin self-updater must find the plugin folder in
the archive under the CURRENT repo layout (``myvault/``).

The repo folder was renamed ``vault/`` -> ``myvault/`` (PR #267), but the
updater's ``performSelfUpdate`` still resolved the archive plugin dir as
``<archiveRoot>/vault/.obsidian/plugins/vaultbot``. Every self-update
since then threw ``'Archive has no plugin folder.'`` — AFTER already
copying the backend (half-applied update, error to the user, plugin never
updated). Nobody noticed because the manifest version was also never
bumped, so ``checkLatestVersion`` never reported an update available in
the first place.

This is a source-level guard, not a runtime test: Electron/Obsidian isn't
available in the CI runner, so we assert on the plugin JS text itself
(same pattern as test_installer_vault_rename_regression.py).

Run: pytest tests/test_updater_archive_paths.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_JS = _REPO_ROOT / "myvault" / ".obsidian" / "plugins" / "vaultbot" / "main.js"
_MANIFEST = (
    _REPO_ROOT / "myvault" / ".obsidian" / "plugins" / "vaultbot" / "manifest.json"
)


@pytest.fixture(scope="module")
def main_js_text() -> str:
    if not _MAIN_JS.exists():
        pytest.skip(f"plugin main.js not found at {_MAIN_JS}")
    return _MAIN_JS.read_text(encoding="utf-8")


def test_updater_resolves_plugin_dir_from_myvault(main_js_text: str) -> None:
    """performSelfUpdate must resolve the archive plugin dir under
    ``myvault/`` (the current layout). A ``vault/``-only path throws
    'Archive has no plugin folder.' on every update since the rename."""
    # The candidate loop must exist and try myvault BEFORE the legacy
    # vault/ name (order matters: current-layout archives win).
    loop_match = re.search(r"for \(const _vaultDir of \[([^\]]+)\]\)", main_js_text)
    assert loop_match, (
        "performSelfUpdate has no candidate loop for the archive vault "
        "dir — it must try ['myvault', 'vault'] in order."
    )
    candidates = [c.strip().strip("'\"") for c in loop_match.group(1).split(",")]
    assert "myvault" in candidates, (
        "performSelfUpdate's archive lookup never tries 'myvault/' — "
        "every self-update throws 'Archive has no plugin folder.' after "
        "applying the backend (half-applied update)."
    )
    if "vault" in candidates:
        assert candidates.index("myvault") < candidates.index("vault"), (
            "performSelfUpdate must try 'myvault/' before the legacy "
            "'vault/' name — current-layout archives must win."
        )
    # And there must be no single-path legacy-only resolution left.
    assert "const srcPlugin = path.join(archiveRoot, 'vault'" not in main_js_text, (
        "performSelfUpdate still resolves the plugin dir solely from the "
        "legacy 'vault/' path — that throws on every current-layout "
        "archive (half-applied update)."
    )


def test_updater_tolerates_legacy_vault_layout(main_js_text: str) -> None:
    """Users can pin a pre-rename tag (e.g. v0.2.0) — its archive still has
    ``vault/``. The updater must fall back to the legacy name instead of
    failing the update."""
    # The legacy lookup must still exist, but only as a FALLBACK —
    # i.e. the myvault lookup must come first (checked by the sibling
    # test), and the vault lookup must not be the sole path.
    assert re.search(
        r"for \(const _vaultDir of \['myvault', 'vault'\]\)", main_js_text
    ), (
        "performSelfUpdate must try ['myvault', 'vault'] in order so "
        "updates pinned to legacy pre-rename tags still work."
    )


def test_manifest_version_is_not_stale() -> None:
    """The plugin manifest version must have been bumped past 1.5.1.

    checkLatestVersion() compares the local manifest version against the
    manifest AT the latest release tag. If main ships the same version as
    the previous release, no install ever sees an update available — the
    release ships and nobody is offered it. Guard the bump that the
    v1.5.2 release prep made.
    """
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    version = data.get("version", "")
    assert version != "1.5.1", (
        "manifest.json is still 1.5.1 — shipping a release with an "
        "unchanged manifest version means checkLatestVersion() reports "
        "no update available for every existing install. Bump it in the "
        "same release-prep PR."
    )
    # Sanity: it must be a semver-ish string, not empty.
    assert re.match(r"^\d+\.\d+\.\d+$", version), f"bad version: {version!r}"
