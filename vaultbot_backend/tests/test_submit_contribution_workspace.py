"""Tests for submit_contribution workspace mode (repo-agnostic issue solving).

When ``VAULTBOT_WORKSPACE_PATH`` is set, submit_contribution operates on
the foreign target repo's git clone instead of the VaultBot repo: git ops
use the workspace root, pre-flight CI gates detect the foreign repo's test
entrypoint generically, and a dangerous-patterns scan runs over the staged
diff before any PR is submitted.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_tools import submit_contribution as sc  # noqa: E402


def _git(repo, *args):
    """Run a git command in a repo, raising on failure."""
    r = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _make_git_repo(tmp_path, name="workspace"):
    """Create a git repo with a commit, returning its path."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture
def workspace_repo(tmp_path, monkeypatch):
    """A git repo pointed at by VAULTBOT_WORKSPACE_PATH."""
    repo = _make_git_repo(tmp_path)
    monkeypatch.setenv("VAULTBOT_WORKSPACE_PATH", str(repo))
    return repo


# ── git root selection ─────────────────────────────────────────────────────


def test_git_root_is_workspace_when_configured(workspace_repo, monkeypatch):
    """In workspace mode, the git root is the workspace clone."""
    from paths import resolve_workspace_root

    assert resolve_workspace_root() == workspace_repo.resolve()


def test_preflight_gates_detect_pytest_in_workspace(workspace_repo):
    """A workspace with pyproject.toml + tests/ gets a pytest gate."""
    (workspace_repo / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n", encoding="utf-8"
    )
    (workspace_repo / "tests").mkdir()
    (workspace_repo / "tests" / "test_x.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )
    gates = sc._run_preflight_ci_gates(str(workspace_repo))
    # pytest gate must be present (pass or fail, but not skipped-without-reason)
    assert "pytest" in gates
    assert gates["pytest"]["status"] in ("pass", "fail", "error")


def test_preflight_gates_skip_unknown_entrypoint(workspace_repo):
    """A workspace with no detectable test entrypoint reports skipped loudly."""
    gates = sc._run_preflight_ci_gates(str(workspace_repo))
    # No pyproject, no tests/, no package.json, no Makefile → pytest skipped
    # with a reason (no silent skip).
    assert "pytest" in gates
    assert gates["pytest"]["status"] == "skipped"
    assert gates["pytest"]["output"]


def test_dangerous_patterns_gate_blocks_eval(workspace_repo):
    """A staged diff adding eval( fails the dangerous-patterns gate."""
    (workspace_repo / "evil.py").write_text(
        "def run():\n    return eval('1+1')\n", encoding="utf-8"
    )
    _git(workspace_repo, "add", "evil.py")
    result = sc._scan_dangerous_patterns(str(workspace_repo))
    assert result["status"] == "fail"
    assert "eval" in result["output"]


def test_dangerous_patterns_gate_passes_clean(workspace_repo):
    """A clean staged diff passes the dangerous-patterns gate."""
    (workspace_repo / "ok.py").write_text(
        "def run():\n    return 1 + 1\n", encoding="utf-8"
    )
    _git(workspace_repo, "add", "ok.py")
    result = sc._scan_dangerous_patterns(str(workspace_repo))
    assert result["status"] == "pass"
