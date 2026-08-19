"""Tests for Safe Mode / Developer Mode gating (safe_mode.py).

Verifies that Safe Mode blocks the right tools:
  - Self-modification tools (safe_write, safe_replace, js_safe_write,
    js_safe_replace, code_run, tool_create, git_rollback, etc.) are blocked.
  - Dual-use edit_lines is NOT blocked as a tool (it edits .md notes too),
    but is_file_edit_allowed() blocks source-code extensions.
  - Safe tools (vault_search, code_read, vault_safe_write, plan_task,
    execute_procedure, etc.) are always allowed.
  - Developer Mode (VAULTBOT_SAFE_MODE=0) allows everything.
"""

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def safe_mode_on(monkeypatch):
    """Enable Safe Mode for the test."""
    monkeypatch.setenv("VAULTBOT_SAFE_MODE", "true")
    # Reimport so module-level state picks up the env var
    import safe_mode

    importlib.reload(safe_mode)
    return safe_mode


@pytest.fixture
def developer_mode(monkeypatch):
    """Enable Developer Mode for the test."""
    monkeypatch.setenv("VAULTBOT_SAFE_MODE", "0")
    import safe_mode

    importlib.reload(safe_mode)
    return safe_mode


# ── Mode detection ──────────────────────────────────────────────────────


class TestModeDetection:
    def test_safe_mode_on_by_default(self, monkeypatch):
        """Safe Mode is ON when VAULTBOT_SAFE_MODE is not set."""
        monkeypatch.delenv("VAULTBOT_SAFE_MODE", raising=False)
        import safe_mode

        importlib.reload(safe_mode)
        assert safe_mode.is_safe_mode() is True

    def test_explicit_true_enables_safe_mode(self, safe_mode_on):
        assert safe_mode_on.is_safe_mode() is True

    @pytest.mark.parametrize("val", ["0", "false", "off", "no", "developer"])
    def test_disabling_values_enter_developer_mode(self, monkeypatch, val):
        monkeypatch.setenv("VAULTBOT_SAFE_MODE", val)
        import safe_mode

        importlib.reload(safe_mode)
        assert safe_mode.is_safe_mode() is False


# ── Dangerous tools blocked in Safe Mode ────────────────────────────────


class TestDangerousToolsBlocked:
    # All tools that MUST be blocked in Safe Mode
    DANGEROUS = [
        "code_write",  # legacy
        "safe_write",
        "js_safe_write",
        "safe_replace",
        "js_safe_replace",
        "code_run",
        "tool_create",
        "git_rollback",
        "backend_restart",
        "plugin_reload",
        "vault_delete",
        "apply_ungating_fix",
        "submit_contribution",
        "review_contributions",
        "torture_test",
    ]

    @pytest.mark.parametrize("tool", DANGEROUS)
    def test_blocked_in_safe_mode(self, safe_mode_on, tool):
        assert safe_mode_on.is_tool_allowed(tool) is False, (
            f"{tool} should be BLOCKED in Safe Mode"
        )

    @pytest.mark.parametrize("tool", DANGEROUS)
    def test_allowed_in_developer_mode(self, developer_mode, tool):
        assert developer_mode.is_tool_allowed(tool) is True, (
            f"{tool} should be ALLOWED in Developer Mode"
        )


# ── Safe tools allowed in Safe Mode ─────────────────────────────────────


class TestSafeToolsAllowed:
    SAFE = [
        "vault_search",
        "vault_read_note",
        "vault_gaps",
        "vaultbot_status",
        "vault_research",
        "vault_safe_write",
        "vault_append",
        "md_safe_replace",
        "plan_task",
        "update_task",
        "add_task",
        "execute_procedure",
        "code_read",
        "edit_lines",  # dual-use: blocked per-file, not per-tool
        "thought",
        "ask_user",
        "web_read_source",
        "textbook_ingest",
        "textbook_read_page",
        "vault_lint",
        "vault_list",
        "vault_graph_analyzer",
        "vault_cluster_analyzer",
        "preflight_safety_check",
        "machine_spec",
        "ollama_model_search",
        "undo_last_write",
    ]

    @pytest.mark.parametrize("tool", SAFE)
    def test_allowed_in_safe_mode(self, safe_mode_on, tool):
        assert safe_mode_on.is_tool_allowed(tool) is True, (
            f"{tool} should be ALLOWED in Safe Mode"
        )


# ── edit_lines content-aware gate (is_file_edit_allowed) ────────────────


class TestFileEditGate:
    # Source-code extensions that MUST be blocked in Safe Mode
    BLOCKED_EXTENSIONS = [
        ".py",
        ".pyw",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".jsx",
        ".tsx",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".sh",
        ".bash",
        ".ps1",
        ".env",
        ".cfg",
        ".ini",
        ".lock",
    ]

    # Non-code extensions that should be ALLOWED in Safe Mode
    ALLOWED_EXTENSIONS = [".md", ".txt", ".csv", ".html", ".pdf", ""]

    @pytest.mark.parametrize("ext", BLOCKED_EXTENSIONS)
    def test_source_code_blocked_in_safe_mode(self, safe_mode_on, ext):
        path = f"vaultbot/vaultbot_backend/foo{ext}"
        assert safe_mode_on.is_file_edit_allowed(path) is False, (
            f"{ext} files must be blocked in Safe Mode (source code)"
        )

    @pytest.mark.parametrize("ext", ALLOWED_EXTENSIONS)
    def test_non_code_allowed_in_safe_mode(self, safe_mode_on, ext):
        path = f"vaultbot/Knowledge/Research/note{ext}"
        assert safe_mode_on.is_file_edit_allowed(path) is True, (
            f"{ext} files should be allowed in Safe Mode"
        )

    @pytest.mark.parametrize("ext", BLOCKED_EXTENSIONS)
    def test_all_files_allowed_in_developer_mode(self, developer_mode, ext):
        path = f"vaultbot/vaultbot_backend/foo{ext}"
        assert developer_mode.is_file_edit_allowed(path) is True

    def test_extension_case_insensitive(self, safe_mode_on):
        """Uppercase extensions should also be blocked."""
        assert safe_mode_on.is_file_edit_allowed("foo.PY") is False
        assert safe_mode_on.is_file_edit_allowed("foo.JS") is False
        assert safe_mode_on.is_file_edit_allowed("foo.JSON") is False

    def test_md_allowed_regardless_of_path(self, safe_mode_on):
        """Even .md files inside vaultbot_backend/ should be allowed."""
        assert (
            safe_mode_on.is_file_edit_allowed("vaultbot/vaultbot_backend/README.md")
            is True
        )


# ── edit_lines integration: Safe Mode blocks .py edits ──────────────────


class TestEditLinesSafeModeIntegration:
    """Integration test: edit_lines.run() blocks .py in Safe Mode."""

    @pytest.fixture(autouse=True)
    def _add_custom_tools_to_path(self):
        """Make edit_lines importable (it lives in custom_tools/)."""
        import sys

        custom_tools_dir = str(Path(__file__).resolve().parent.parent / "custom_tools")
        if custom_tools_dir not in sys.path:
            sys.path.insert(0, custom_tools_dir)
        yield
        # Clean up: remove if we added it
        if custom_tools_dir in sys.path:
            sys.path.remove(custom_tools_dir)

    def test_edit_lines_blocks_py_in_safe_mode(
        self, safe_mode_on, tmp_path, monkeypatch
    ):
        """edit_lines should refuse to edit a .py file in Safe Mode."""
        # Create a temp .py file inside a fake vault root
        py_file = tmp_path / "test_code.py"
        py_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

        # Monkeypatch edit_lines' VAULT_ROOT to our tmp_path
        import edit_lines

        monkeypatch.setattr(edit_lines, "VAULT_ROOT", tmp_path)

        result = edit_lines.run(
            {
                "file_path": "test_code.py",
                "start_line": 1,
                "end_line": 1,
                "new_content": "def bar():",
            }
        )

        assert result.get("status") == "blocked"
        assert result.get("safe_mode_blocked") is True
        # File should be unchanged
        assert py_file.read_text(encoding="utf-8") == "def foo():\n    return 1\n"

    def test_edit_lines_allows_md_in_safe_mode(
        self, safe_mode_on, tmp_path, monkeypatch
    ):
        """edit_lines should allow .md edits in Safe Mode."""
        md_content = (
            "---\n"
            "type: concept\n"
            "status: active\n"
            "created: 2026-01-01\n"
            "summary: A test note\n"
            "tags: [test]\n"
            "---\n"
            "# Title\n\nSome content.\n"
        )
        md_file = tmp_path / "note.md"
        md_file.write_text(md_content, encoding="utf-8")

        import edit_lines

        monkeypatch.setattr(edit_lines, "VAULT_ROOT", tmp_path)
        # Also patch TRASH_DIR so backup doesn't write to real trash
        monkeypatch.setattr(edit_lines, "TRASH_DIR", tmp_path / "trash")

        # Replace the body line "# Title" (line 8) with a new heading.
        result = edit_lines.run(
            {
                "file_path": "note.md",
                "start_line": 8,
                "end_line": 8,
                "new_content": "# New Title",
            }
        )

        assert result.get("status") == "written", f"Expected written, got: {result}"
        assert "safe_mode_blocked" not in result

    def test_edit_lines_allows_py_in_developer_mode(
        self, developer_mode, tmp_path, monkeypatch
    ):
        """edit_lines should allow .py edits in Developer Mode (no safe_mode block)."""
        py_file = tmp_path / "test_code.py"
        py_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

        import edit_lines

        monkeypatch.setattr(edit_lines, "VAULT_ROOT", tmp_path)
        monkeypatch.setattr(edit_lines, "TRASH_DIR", tmp_path / "trash")

        # In developer mode, the safe_mode gate import will still import
        # safe_mode, but is_file_edit_allowed returns True. We use dry_run
        # so safe_write's syntax check + import verification doesn't need
        # the full backend on sys.path.
        result = edit_lines.run(
            {
                "file_path": "test_code.py",
                "start_line": 1,
                "end_line": 1,
                "new_content": "def bar():",
                "dry_run": True,
            }
        )
        # The key assertion: NOT blocked by safe mode
        assert result.get("safe_mode_blocked") is not True
        assert (
            result.get("status") != "blocked"
            or result.get("safe_mode_blocked") is not True
        )
