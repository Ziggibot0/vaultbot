"""Tests for the code_run read-only guard (issue #207, Gap 2).

Verifies that ``code_run`` blocks file-write primitives by default and
allows them when ``allow_write=True``. Uses a real subprocess (the venv
python) so the guard preamble actually executes, but writes only to a
throwaway tmp dir — never the live backend.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

import self_improver


@pytest.fixture
def improver(tmp_path, monkeypatch):
    """A SelfImprover whose BACKEND_ROOT points at a throwaway tmp tree."""
    backend_dir = tmp_path / "vaultbot_backend"
    backend_dir.mkdir()
    custom_tools = backend_dir / "custom_tools"
    custom_tools.mkdir()
    (custom_tools / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(self_improver, "BACKEND_ROOT", tmp_path, raising=True)
    monkeypatch.setattr(self_improver, "BACKEND_DIR", backend_dir, raising=True)
    monkeypatch.setattr(self_improver, "CUSTOM_TOOLS_DIR", custom_tools, raising=True)
    return self_improver.SelfImprover()


class TestCodeRunReadOnlyGuard:
    def test_open_write_is_blocked(self, improver, tmp_path):
        target = tmp_path / "should_not_exist.txt"
        code = f"open({str(target)!r}, 'w').write('x')"
        result = improver.code_run(code)
        # The write must be blocked — either a PermissionError surfaced in
        # stderr, or a non-zero exit code.
        assert not target.exists(), "code_run wrote a file despite the guard"
        assert result.get("exit_code") != 0 or "PermissionError" in result.get(
            "stderr", ""
        )

    def test_path_write_text_is_blocked(self, improver, tmp_path):
        target = tmp_path / "should_not_exist2.txt"
        code = f"from pathlib import Path\nPath({str(target)!r}).write_text('x')\n"
        result = improver.code_run(code)
        assert not target.exists()
        assert result.get("exit_code") != 0 or "PermissionError" in result.get(
            "stderr", ""
        )

    def test_shutil_copy_is_blocked(self, improver, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        code = f"import shutil\nshutil.copy({str(src)!r}, {str(dst)!r})\n"
        result = improver.code_run(code)
        assert not dst.exists()
        assert result.get("exit_code") != 0 or "PermissionError" in result.get(
            "stderr", ""
        )

    def test_read_only_code_still_runs(self, improver):
        # Reading and pure computation must still work under the guard.
        result = improver.code_run("print(1 + 1)")
        assert result.get("exit_code") == 0
        assert "2" in result.get("stdout", "")

    def test_allow_write_skips_guard(self, improver, tmp_path):
        target = tmp_path / "allowed.txt"
        code = f"open({str(target)!r}, 'w').write('x')"
        result = improver.code_run(code, allow_write=True)
        assert result.get("exit_code") == 0
        assert target.exists()
