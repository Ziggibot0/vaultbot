"""Tests for the vault-root directive guard in vault_safe_write.

VaultBot must never write its own directives to the vault root. Directives
belong under vaultbot/System/Identity/. The `_is_root_directive` helper is
the hard guard (not just a prompt hint) that blocks root-level
`*-Directive.md` and `Communication-Preferences.md` writes.
"""

import sys
from pathlib import Path

import pytest

# Import the custom tool module directly (leaf module, no main import).
_CUSTOM_TOOLS = Path(__file__).resolve().parent.parent / "custom_tools"
sys.path.insert(0, str(_CUSTOM_TOOLS))

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
        # Nested under vaultbot/System/Identity/ — allowed.
        ("vaultbot/System/Identity/Autonomy-Directive.md", False),
        ("vaultbot/System/Identity/IDK-Fallback-Directive.md", False),
        ("vaultbot/System/Identity/Sean-Communication-Preferences.md", False),
        # Normal user notes — allowed.
        ("User/My-Note.md", False),
        ("User/Research-Roadmap.md", False),
        ("vaultbot/Knowledge/Research/My-Note.md", False),
        # Windows-style separators still resolve to root-level.
        ("Autonomy-Directive.md", True),
    ],
)
def test_is_root_directive(path, expected):
    assert vault_safe_write._is_root_directive(path) is expected


def test_run_blocks_root_directive(tmp_path, monkeypatch):
    """A root-level directive write is blocked before touching disk."""
    monkeypatch.setattr(vault_safe_write, "VAULT_ROOT", tmp_path)
    result = vault_safe_write.run(
        {"file_path": "Autonomy-Directive.md", "content": "# Autonomy\n"}
    )
    assert result["status"] == "blocked"
    assert "Root-level directive blocked" in result["blocked_reason"]
    assert not (tmp_path / "Autonomy-Directive.md").exists()


def test_run_allows_nested_directive(tmp_path, monkeypatch):
    """A directive under vaultbot/System/Identity/ is allowed."""
    monkeypatch.setattr(vault_safe_write, "VAULT_ROOT", tmp_path)
    target = tmp_path / "vaultbot" / "System" / "Identity"
    target.mkdir(parents=True, exist_ok=True)
    result = vault_safe_write.run(
        {
            "file_path": "vaultbot/System/Identity/Autonomy-Directive.md",
            "content": "# Autonomy\n",
        }
    )
    assert result["status"] == "written"
    assert (target / "Autonomy-Directive.md").exists()


def test_vault_append_blocks_root_directive(tmp_path, monkeypatch):
    """vault_append must also block root-level directive writes."""
    monkeypatch.setattr(vault_append, "VAULT_ROOT", tmp_path)
    result = vault_append.run(
        {"file_path": "Autonomy-Directive.md", "content": "# Autonomy\n"}
    )
    assert "Root-level directive blocked" in result["error"]
    assert not (tmp_path / "Autonomy-Directive.md").exists()


def test_vault_append_allows_nested_directive(tmp_path, monkeypatch):
    """vault_append allows a directive under vaultbot/System/Identity/."""
    monkeypatch.setattr(vault_append, "VAULT_ROOT", tmp_path)
    target = tmp_path / "vaultbot" / "System" / "Identity"
    target.mkdir(parents=True, exist_ok=True)
    result = vault_append.run(
        {
            "file_path": "vaultbot/System/Identity/Autonomy-Directive.md",
            "content": "# Autonomy\n",
        }
    )
    assert "error" not in result
    assert (target / "Autonomy-Directive.md").exists()
