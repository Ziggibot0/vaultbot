"""Tests for self_improver.tool_create syntax guard.

The tool_create method writes agent-authored Python to custom_tools/.
Without a syntax check, a broken tool file crashes the hot-reload on the
next tool_create call.  The fix adds ast.parse() before writing — bad
syntax is rejected and the file is NOT written to disk.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_tool_create_rejects_syntax_error(tmp_path, monkeypatch):
    """A tool with invalid Python syntax is rejected before writing."""
    from self_improver import SelfImprover

    # Point CUSTOM_TOOLS_DIR at a temp dir so we don't touch real tools.
    custom_dir = tmp_path / "custom_tools"
    custom_dir.mkdir()

    improver = SelfImprover.__new__(SelfImprover)
    improver._loaded_schemas = {}
    improver.session_logger = MagicMock()

    # Monkeypatch the module-level CUSTOM_TOOLS_DIR.
    import self_improver as si_mod

    monkeypatch.setattr(si_mod, "CUSTOM_TOOLS_DIR", custom_dir)

    # Also patch _safe_name to just return the input.
    monkeypatch.setattr(
        SelfImprover,
        "_safe_name",
        staticmethod(lambda name: name.replace(" ", "_").lower()),
    )

    result = improver.tool_create(
        tool_name="broken_tool",
        description="A tool with bad syntax",
        parameters={"type": "object", "properties": {}},
        code="def run(args):\n    return {bad syntax here}",  # syntax error
    )

    assert "error" in result
    assert "syntax error" in result["error"].lower()
    # The file must NOT exist — it was rejected before writing.
    assert not (custom_dir / "broken_tool.py").exists()


def test_tool_create_accepts_valid_code(tmp_path, monkeypatch):
    """A tool with valid Python is written to disk successfully."""
    from self_improver import SelfImprover

    custom_dir = tmp_path / "custom_tools"
    custom_dir.mkdir()

    improver = SelfImprover.__new__(SelfImprover)
    improver._loaded_schemas = {}
    improver.session_logger = MagicMock()

    import self_improver as si_mod

    monkeypatch.setattr(si_mod, "CUSTOM_TOOLS_DIR", custom_dir)
    monkeypatch.setattr(
        SelfImprover,
        "_safe_name",
        staticmethod(lambda name: name.replace(" ", "_").lower()),
    )

    # Patch load_custom_tools so it doesn't try to import real modules.
    monkeypatch.setattr(SelfImprover, "load_custom_tools", lambda self: None)

    valid_code = "def run(args):\n    return {'status': 'ok'}\n"
    result = improver.tool_create(
        tool_name="good_tool",
        description="A valid tool",
        parameters={"type": "object", "properties": {}},
        code=valid_code,
    )

    # The file should exist (syntax was valid, write succeeded).
    assert (custom_dir / "good_tool.py").exists()
    # The result should NOT be a syntax error.
    assert "syntax error" not in str(result).lower()
