"""Unit tests for the codebase_map custom tool.

Covers the pure-logic surface with NO network access: the map note is
written to a tmp_path and the tool reads it back. Only the leaf module
`custom_tools.codebase_map` is imported — never `main`.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import codebase_map as cm


def _point_at_tmp(tmp_path, monkeypatch):
    """Patch cm.__file__ so run() resolves its map under tmp_path.

    run() derives backend_dir = Path(__file__).parent.parent and
    vault_root = backend_dir.parent.parent. Pointing __file__ at
    tmp_path/vaultbot/vaultbot_backend/custom_tools/codebase_map.py makes
    vault_root == tmp_path, so the map resolves to
    tmp_path/vaultbot/Knowledge/Architecture/Codebase-Map.md.
    """
    fake_file = (
        tmp_path / "vaultbot" / "vaultbot_backend" / "custom_tools" / "codebase_map.py"
    )
    monkeypatch.setattr(cm, "__file__", str(fake_file))
    return tmp_path / "vaultbot" / "Knowledge" / "Architecture" / "Codebase-Map.md"


def _write_map(map_path, content):
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(content, encoding="utf-8")


def test_missing_map_returns_error(tmp_path, monkeypatch):
    _point_at_tmp(tmp_path, monkeypatch)
    # Do NOT write the map — the tool should report it's missing.
    result = cm.run({})
    assert "error" in result
    assert "Codebase-Map" in result["error"] or "not found" in result["error"]


def test_full_map_returned(tmp_path, monkeypatch):
    map_path = _point_at_tmp(tmp_path, monkeypatch)
    content = (
        "# Codebase-Map\n\n"
        "## chat_handler\n\n**Purpose:** handles chat\n\n"
        "## vault_indexer\n\n**Purpose:** indexes\n"
    )
    _write_map(map_path, content)

    result = cm.run({})
    assert result["status"] == "success"
    assert result["module"] is None
    assert "chat_handler" in result["content"]
    assert "vault_indexer" in result["content"]


def test_single_module_section(tmp_path, monkeypatch):
    map_path = _point_at_tmp(tmp_path, monkeypatch)
    content = (
        "# Codebase-Map\n\n"
        "## chat_handler\n\n**Purpose:** handles chat\n\n"
        "## vault_indexer\n\n**Purpose:** indexes\n"
    )
    _write_map(map_path, content)

    result = cm.run({"module": "chat_handler"})
    assert result["status"] == "success"
    assert result["module"] == "chat_handler"
    assert "handles chat" in result["content"]
    # Must NOT include the next module's section.
    assert "vault_indexer" not in result["content"]


def test_unknown_module_returns_error(tmp_path, monkeypatch):
    map_path = _point_at_tmp(tmp_path, monkeypatch)
    content = "# Codebase-Map\n\n## chat_handler\n\n**Purpose:** handles chat\n"
    _write_map(map_path, content)

    result = cm.run({"module": "does_not_exist"})
    assert "error" in result
