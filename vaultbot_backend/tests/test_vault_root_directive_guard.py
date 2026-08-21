"""Tests for the vault-root directive guard in vault_safe_write.

VaultBot must never write its own directives to the vault root. Directives
belong under System/Identity/ (framework root). The `_is_root_directive`
helper is the hard guard (not just a prompt hint) that blocks root-level
`*-Directive.md` and `Communication-Preferences.md` writes.
"""

import sys
from pathlib import Path

import pytest

# Import the custom tool module directly (leaf module, no main import).
_CUSTOM_TOOLS = Path(__file__).resolve().parent.parent / "custom_tools"
sys.path.insert(0, str(_CUSTOM_TOOLS))

import paths  # noqa: E402
import vault_append  # noqa: E402
import vault_safe_write  # noqa: E402


@pytest.mark.parametrize(
    "path,expected",
    [
        # Root-level directives — MUST be blocked.
        ("Autonomy-Directive.md", True),
        ("IDK-Fallback-Directive.md", True),
        ("No-Wikipedia-Directive.md", True),
        ("Vault-Knowledge-Only-Directive.md", True),
        ("Communication-Preferences.md", True),
        ("Sean-Communication-Preferences.md", True),
        # Nested under System/Identity/ — allowed.
        ("System/Identity/Autonomy-Directive.md", False),
        ("System/Identity/IDK-Fallback-Directive.md", False),
        ("System/Identity/Sean-Communication-Preferences.md", False),
        # Normal user notes — allowed.
        ("User/My-Note.md", False),
        ("User/Research-Roadmap.md", False),
        ("Knowledge/Research/My-Note.md", False),
        # Windows-style separators still resolve to root-level.
        ("Autonomy-Directive.md", True),
    ],
)
def test_is_root_directive(path, expected):
    assert vault_safe_write._is_root_directive(path) is expected


def _patch_roots(tmp_path, monkeypatch):
    """Point paths' roots at tmp_path so writes land in the temp dir."""
    monkeypatch.setattr(paths, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(paths, "FRAMEWORK_ROOT", tmp_path)


def test_run_blocks_root_directive(tmp_path, monkeypatch):
    """A root-level directive write is blocked before touching disk."""
    _patch_roots(tmp_path, monkeypatch)
    result = vault_safe_write.run(
        {"file_path": "Autonomy-Directive.md", "content": "# Autonomy\n"}
    )
    assert result["status"] == "blocked"
    assert "Root-level directive blocked" in result["blocked_reason"]
    assert not (tmp_path / "Autonomy-Directive.md").exists()


def test_run_allows_nested_directive(tmp_path, monkeypatch):
    """A directive under System/Identity/ is allowed."""
    _patch_roots(tmp_path, monkeypatch)
    target = tmp_path / "System" / "Identity"
    target.mkdir(parents=True, exist_ok=True)
    result = vault_safe_write.run(
        {
            "file_path": "System/Identity/Autonomy-Directive.md",
            "content": "# Autonomy\n",
        }
    )
    assert result["status"] == "written"
    assert (target / "Autonomy-Directive.md").exists()


def test_vault_append_blocks_root_directive(tmp_path, monkeypatch):
    """vault_append must also block root-level directive writes."""
    _patch_roots(tmp_path, monkeypatch)
    result = vault_append.run(
        {"file_path": "Autonomy-Directive.md", "content": "# Autonomy\n"}
    )
    assert "Root-level directive blocked" in result["error"]
    assert not (tmp_path / "Autonomy-Directive.md").exists()


def test_vault_append_allows_nested_directive(tmp_path, monkeypatch):
    """vault_append allows a directive under System/Identity/."""
    _patch_roots(tmp_path, monkeypatch)
    target = tmp_path / "System" / "Identity"
    target.mkdir(parents=True, exist_ok=True)
    result = vault_append.run(
        {
            "file_path": "System/Identity/Autonomy-Directive.md",
            "content": "# Autonomy\n",
        }
    )
    assert "error" not in result
    assert (target / "Autonomy-Directive.md").exists()
