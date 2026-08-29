"""Tests for the configurable workspace root (repo-agnostic issue solving).

The workspace root is where a target project's git clone lives. When
``VAULTBOT_WORKSPACE_PATH`` is set, read/edit/test/PR tools resolve against
it in addition to the vault and framework roots. When it is unset, behavior
is byte-identical to today (legacy single-root mode) — this is the
non-negotiable regression guard.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

import paths


@pytest.fixture
def no_workspace(monkeypatch):
    """Ensure VAULTBOT_WORKSPACE_PATH is unset (legacy mode)."""
    monkeypatch.delenv("VAULTBOT_WORKSPACE_PATH", raising=False)
    return paths


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point VAULTBOT_WORKSPACE_PATH at a temp dir."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("VAULTBOT_WORKSPACE_PATH", str(ws))
    return ws


# ── resolve_workspace_root ────────────────────────────────────────────────


def test_workspace_root_unset_is_framework_root(no_workspace):
    """Unset env → FRAMEWORK_ROOT (legacy mode, zero behavior change)."""
    assert paths.resolve_workspace_root() == paths.FRAMEWORK_ROOT


def test_workspace_root_absolute(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("VAULTBOT_WORKSPACE_PATH", str(ws))
    assert paths.resolve_workspace_root() == ws.resolve()


def test_workspace_root_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTBOT_WORKSPACE_PATH", "rel_ws")
    assert paths.resolve_workspace_root() == (paths.FRAMEWORK_ROOT / "rel_ws").resolve()


def test_workspace_root_nonexistent_still_resolves(tmp_path, monkeypatch):
    """A nonexistent workspace path still resolves (creates are allowed)."""
    ws = tmp_path / "does_not_exist"
    monkeypatch.setenv("VAULTBOT_WORKSPACE_PATH", str(ws))
    assert paths.resolve_workspace_root() == ws.resolve()


# ── resolve_write_path: legacy mode unchanged ─────────────────────────────


def test_write_path_legacy_repo_file_resolves(no_workspace):
    """A repo-root file resolves in legacy mode (unchanged behavior)."""
    result = paths.resolve_write_path("vaultbot_backend/main.py")
    assert result is not None
    assert result == (paths.FRAMEWORK_ROOT / "vaultbot_backend/main.py").resolve()


def test_write_path_legacy_vault_note_resolves(no_workspace):
    """A vault note resolves in legacy mode (unchanged behavior)."""
    rel = "vaultbot-stuff/System/Procedures/Build-Procedure.md"
    result = paths.resolve_write_path(rel)
    assert result is not None
    assert result == (paths._resolve_vault_root() / rel).resolve()


def test_write_path_legacy_escape_returns_none(no_workspace):
    """A path escaping both roots returns None in legacy mode."""
    assert paths.resolve_write_path("../../outside") is None


# ── resolve_write_path: workspace mode ────────────────────────────────────


def test_write_path_workspace_existing_file(workspace):
    """A file that exists only under the workspace resolves there."""
    (workspace / "src").mkdir()
    (workspace / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    result = paths.resolve_write_path("src/foo.py")
    assert result == (workspace / "src" / "foo.py").resolve()


def test_write_path_workspace_create_goes_to_workspace(workspace):
    """A brand-new file in workspace mode resolves under the workspace root."""
    result = paths.resolve_write_path("new_module.py")
    assert result == (workspace / "new_module.py").resolve()


def test_write_path_workspace_escape_returns_none(workspace):
    """A path escaping all three roots returns None in workspace mode."""
    assert paths.resolve_write_path("../../outside") is None


# ── is_within_content_roots ───────────────────────────────────────────────


def test_is_within_workspace_root(workspace):
    (workspace / "file.py").write_text("", encoding="utf-8")
    assert paths.is_within_content_roots(str(workspace / "file.py")) is True


def test_is_within_workspace_root_escape(workspace):
    assert paths.is_within_content_roots(str(workspace / ".." / "outside")) is False


# ── safe_writer.resolve_path (choke point for code_read/code_write/safe_write) ──


def test_safe_writer_resolve_workspace_file(workspace):
    """A file existing only under the workspace resolves there (read path)."""
    import safe_writer

    (workspace / "src").mkdir()
    (workspace / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    result = safe_writer.resolve_path("src/foo.py", paths.FRAMEWORK_ROOT)
    assert result == (workspace / "src" / "foo.py").resolve()


def test_safe_writer_resolve_legacy_repo_file(no_workspace):
    """Without a workspace, a repo file resolves against the framework root."""
    import safe_writer

    result = safe_writer.resolve_path("vaultbot_backend/main.py", paths.FRAMEWORK_ROOT)
    assert result == (paths.FRAMEWORK_ROOT / "vaultbot_backend/main.py").resolve()


def test_safe_writer_resolve_escape_returns_none(workspace):
    """A path escaping all roots returns None."""
    import safe_writer

    assert safe_writer.resolve_path("../../outside", paths.FRAMEWORK_ROOT) is None


def test_safe_writer_resolve_create_goes_to_workspace(workspace):
    """A brand-new file in workspace mode resolves under the workspace root."""
    import safe_writer

    result = safe_writer.resolve_path(
        "new_module.py", paths.FRAMEWORK_ROOT, allow_create=True
    )
    assert result == (workspace / "new_module.py").resolve()


def test_safe_writer_resolve_workspace_md_file(workspace):
    """A foreign repo's .md file resolves under the workspace (not a vault note)."""
    import safe_writer

    (workspace / "PROJECT_README.md").write_text("# hi\n", encoding="utf-8")
    result = safe_writer.resolve_path("PROJECT_README.md", paths.FRAMEWORK_ROOT)
    assert result == (workspace / "PROJECT_README.md").resolve()


# ── edit_lines (inherits workspace via resolve_write_path) ─────────────────


def test_edit_lines_edits_workspace_file(workspace, monkeypatch):
    """edit_lines can edit a file that lives only under the workspace root."""
    from custom_tools import edit_lines

    (workspace / "notes.md").write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = edit_lines.run(
        {
            "file_path": "notes.md",
            "start_line": 2,
            "end_line": 2,
            "new_content": "line2-EDITED",
        }
    )
    assert result["status"] == "written"
    assert (workspace / "notes.md").read_text(encoding="utf-8") == (
        "line1\nline2-EDITED\nline3\n"
    )


def test_edit_lines_workspace_escape_blocked(workspace):
    """edit_lines rejects a path escaping all roots."""
    from custom_tools import edit_lines

    result = edit_lines.run(
        {
            "file_path": "../../outside.md",
            "start_line": 1,
            "end_line": 1,
            "new_content": "x",
        }
    )
    assert result["status"] == "error"
    assert "Path traversal" in result["error"]
