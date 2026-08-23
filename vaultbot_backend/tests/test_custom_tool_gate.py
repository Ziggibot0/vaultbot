"""Tests for the agent-authored custom-tool security gate (issue #228).

Covers two layers:
  1. ``custom_tool_gate.gate_agent_tool_code`` — the pure detector/rejector.
  2. ``SelfImprover.tool_create`` — that the gate actually blocks the file
     write (and still allows doc-sourced or safe tools).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ── Direct gate tests ────────────────────────────────────────────────────


def test_gate_rejects_os_import_without_doc_source(tmp_path):
    import custom_tool_gate as g

    result = g.gate_agent_tool_code(
        "import os\ndef run(args):\n    return os.environ\n", tmp_path
    )
    assert result["status"] == "rejected"
    assert "os" in result["dangerous_imports"]
    assert "error" in result


def test_gate_rejects_socket_import_without_doc_source(tmp_path):
    import custom_tool_gate as g

    result = g.gate_agent_tool_code(
        "import socket\ndef run(args):\n    return {}\n", tmp_path
    )
    assert result["status"] == "rejected"
    assert "socket" in result["dangerous_imports"]


def test_gate_rejects_requests_import_without_doc_source(tmp_path):
    import custom_tool_gate as g

    result = g.gate_agent_tool_code(
        "import requests\ndef run(args):\n    return requests.get('x')\n", tmp_path
    )
    assert result["status"] == "rejected"
    assert "requests" in result["dangerous_imports"]


def test_gate_allows_safe_pure_logic(tmp_path):
    import custom_tool_gate as g

    result = g.gate_agent_tool_code(
        "import json\ndef run(args):\n    return json.loads(args['x'])\n", tmp_path
    )
    assert result["status"] == "ok"
    assert result["dangerous_imports"] == []


def test_gate_allows_dangerous_with_doc_source(tmp_path):
    import custom_tool_gate as g

    result = g.gate_agent_tool_code(
        "import os\ndef run(args):\n    return {}\n",
        tmp_path,
        doc_source="https://docs.python.org/3/library/os.html",
    )
    assert result["status"] == "ok"
    # The dangerous import is still recorded so the caller can log it.
    assert "os" in result["dangerous_imports"]


def test_gate_does_not_flag_internal_vaultbot_modules(tmp_path):
    """A tool importing a real VaultBot backend module (e.g. config) must NOT
    be treated as dangerous — internal modules are excluded by
    detect_external_imports."""
    import custom_tool_gate as g
    from self_improver import BACKEND_DIR

    result = g.gate_agent_tool_code(
        "from config import TUNABLES\ndef run(args):\n    return {'ok': True}\n",
        BACKEND_DIR,
    )
    assert result["status"] == "ok"


# ── tool_create integration tests ────────────────────────────────────────


def _make_improver(tmp_path, monkeypatch):
    import self_improver as si_mod
    from self_improver import SelfImprover

    custom_dir = tmp_path / "custom_tools"
    custom_dir.mkdir()
    improver = SelfImprover.__new__(SelfImprover)
    improver._loaded_schemas = {}
    improver.session_logger = MagicMock()
    monkeypatch.setattr(si_mod, "CUSTOM_TOOLS_DIR", custom_dir)
    monkeypatch.setattr(
        SelfImprover,
        "_safe_name",
        staticmethod(lambda name: name.replace(" ", "_").lower()),
    )
    monkeypatch.setattr(SelfImprover, "load_custom_tools", lambda self: None)
    return improver, custom_dir


def test_tool_create_blocks_dangerous_tool_and_writes_nothing(tmp_path, monkeypatch):
    improver, custom_dir = _make_improver(tmp_path, monkeypatch)
    result = improver.tool_create(
        tool_name="leak",
        description="exfil",
        parameters={"type": "object", "properties": {}},
        code="import os\ndef run(args):\n    return os.environ\n",
    )
    assert result["status"] == "rejected"
    assert (custom_dir / "leak.py").exists() is False


def test_tool_create_allows_dangerous_tool_with_doc_source(tmp_path, monkeypatch):
    improver, custom_dir = _make_improver(tmp_path, monkeypatch)
    result = improver.tool_create(
        tool_name="os_tool",
        description="reads cwd",
        parameters={"type": "object", "properties": {}},
        code="import os\ndef run(args):\n    return {'cwd': os.getcwd()}\n",
        doc_source="https://docs.python.org/3/library/os.html",
    )
    assert result.get("status") != "rejected"
    assert (custom_dir / "os_tool.py").exists()


def test_tool_create_allows_safe_tool(tmp_path, monkeypatch):
    improver, custom_dir = _make_improver(tmp_path, monkeypatch)
    result = improver.tool_create(
        tool_name="calc",
        description="adds",
        parameters={"type": "object", "properties": {}},
        code="def run(args):\n    return {'sum': args['a'] + args['b']}\n",
    )
    assert result.get("status") != "rejected"
    assert (custom_dir / "calc.py").exists()
