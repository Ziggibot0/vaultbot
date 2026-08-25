"""Tests for the code_run guard (issues #207 and #229).

Verifies that ``code_run`` blocks:
- file writes (issue #207),
- network egress (issue #229), and
- reads of secret/credential files (.env, providers.json, *_tokens.json,
  *_config.json) (issue #229),

and allows writes when ``allow_write=True``. Uses a real subprocess (the
venv python) so the guard preamble actually executes, but writes only to a
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

    def test_truncation_metadata_is_returned(self, improver):
        result = improver.code_run("print('x' * 5000)")
        assert result.get("exit_code") == 0
        assert "stdout_head" in result
        assert "stdout_tail" in result
        assert "stdout_total_bytes" in result
        assert "stdout_total_lines" in result
        assert "truncated" in result
        assert result["truncated"] is False or result["stdout_total_bytes"] > 0
        if result["truncated"]:
            assert "output truncated" in result.get("stdout", "").lower()


class TestCodeRunNetworkIsolation:
    """issue #229: code_run must not be able to open the network."""

    def test_requests_import_is_blocked(self, improver):
        code = "import requests\nprint(1)"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "network" in result.get("stderr", "").lower()

    def test_socket_import_is_blocked(self, improver):
        code = "import socket\nprint(1)"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "network" in result.get("stderr", "").lower()

    def test_urllib_import_is_blocked(self, improver):
        code = "import urllib.request\nprint(1)"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "network" in result.get("stderr", "").lower()

    def test_http_client_import_is_blocked(self, improver):
        code = "import http.client\nprint(1)"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "network" in result.get("stderr", "").lower()

    def test_plain_import_still_works(self, improver):
        # Non-network stdlib imports must keep working.
        result = improver.code_run("import os, json, sys\nprint('ok')")
        assert result.get("exit_code") == 0
        assert "ok" in result.get("stdout", "")


class TestCodeRunSecretFileReads:
    """issue #229: code_run must not be able to read secret/credential files."""

    def test_env_file_read_is_blocked(self, improver, tmp_path):
        secret = tmp_path / ".env"
        secret.write_text("API_KEY=supersecret", encoding="utf-8")
        code = f"print(open({str(secret)!r}).read())"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "secret" in result.get("stderr", "").lower()
        assert "supersecret" not in result.get("stdout", "")

    def test_providers_json_read_is_blocked(self, improver, tmp_path):
        providers = tmp_path / "providers.json"
        providers.write_text('{"key": "abc"}', encoding="utf-8")
        code = f"from pathlib import Path\nprint(Path({str(providers)!r}).read_text())"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "abc" not in result.get("stdout", "")

    def test_tokens_json_read_is_blocked(self, improver, tmp_path):
        tokens = tmp_path / "github_tokens.json"
        tokens.write_text('{"gh": "abc"}', encoding="utf-8")
        code = f"open({str(tokens)!r}).read()"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "abc" not in result.get("stdout", "")

    def test_config_json_read_is_blocked(self, improver, tmp_path):
        config = tmp_path / "app_config.json"
        config.write_text('{"token": "xyz"}', encoding="utf-8")
        code = f"open({str(config)!r}).read()"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "xyz" not in result.get("stdout", "")

    def test_unrelated_json_outside_repo_still_reads(self, improver, tmp_path):
        # A plain config.json OUTSIDE the repo root must NOT be blocked —
        # scoping avoids false positives on package fixtures.
        outside = tmp_path / "outside"
        outside.mkdir()
        cfg = outside / "config.json"
        cfg.write_text('{"data": "value"}', encoding="utf-8")
        code = f"print(open({str(cfg)!r}).read())"
        result = improver.code_run(code)
        assert result.get("exit_code") == 0
        assert "value" in result.get("stdout", "")

    def test_env_read_blocked_even_outside_repo(self, improver, tmp_path):
        # .env is ALWAYS protected regardless of location (it is a secret
        # filename, not a repo-scoped one).
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / ".env"
        secret.write_text("SECRET=leak", encoding="utf-8")
        code = f"open({str(secret)!r}).read()"
        result = improver.code_run(code)
        assert result.get("exit_code") != 0
        assert "leak" not in result.get("stdout", "")
