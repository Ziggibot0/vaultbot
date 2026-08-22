"""Tests for the code_run file-write guard (issue #207 Gap 2).

code_run is for TESTING only. It must reject code that performs file
writes so the only path to modify backend source is the gated safe_write.
The guard is an AST-based static check in self_improver._contains_file_write,
invoked at the top of SelfImprover.code_run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _make_improver():
    from self_improver import SelfImprover

    improver = SelfImprover.__new__(SelfImprover)
    improver.session_logger = MagicMock()
    return improver


# ── _contains_file_write ───────────────────────────────────────────────────


def test_open_write_mode_blocked():
    from self_improver import _contains_file_write

    assert _contains_file_write('open("x.txt", "w")') is not None
    assert _contains_file_write('open("x.txt", "a")') is not None
    assert _contains_file_write('open("x.txt", "x")') is not None
    assert _contains_file_write('open("x.txt", "wb")') is not None


def test_open_read_mode_allowed():
    from self_improver import _contains_file_write

    assert _contains_file_write('open("x.txt", "r")') is None
    assert _contains_file_write('open("x.txt")') is None
    assert _contains_file_write('open("x.txt", "rb")') is None


def test_write_method_blocked():
    from self_improver import _contains_file_write

    assert _contains_file_write("f.write('hi')") is not None
    assert _contains_file_write("f.writelines(lines)") is not None
    assert _contains_file_write("f.truncate()") is not None


def test_pathlib_mkdir_unlink_blocked():
    from self_improver import _contains_file_write

    assert _contains_file_write("Path('d').mkdir()") is not None
    assert _contains_file_write("p.unlink()") is not None
    # os.remove is an attribute call .remove -> blocked.
    assert _contains_file_write("os.remove('x')") is not None


def test_shutil_copy_blocked():
    from self_improver import _contains_file_write

    assert _contains_file_write("shutil.copy('a', 'b')") is not None
    assert _contains_file_write("shutil.move('a', 'b')") is not None


def test_pure_read_code_allowed():
    from self_improver import _contains_file_write

    assert _contains_file_write("print(1 + 1)") is None
    assert _contains_file_write("x = [i for i in range(10)]\nprint(x)") is None
    assert _contains_file_write("data = open('f.csv').read()\nprint(data)") is None


def test_syntax_error_returns_reason():
    from self_improver import _contains_file_write

    reason = _contains_file_write("def f(:")
    assert reason is not None
    assert "syntax error" in reason


# ── SelfImprover.code_run integration (no subprocess spawned) ──────────────


def test_code_run_blocks_open_write(monkeypatch):
    improver = _make_improver()
    # If the guard works, _subprocess_run is never called.
    import self_improver as si_mod

    called = {"v": False}

    def _boom(*a, **k):
        called["v"] = True
        return "should not run"

    monkeypatch.setattr(si_mod, "_subprocess_run", _boom)
    result = improver.code_run('open("out.txt", "w").write("nope")')
    assert "error" in result
    assert "blocked" in result["error"]
    assert called["v"] is False


def test_code_run_blocks_write_method(monkeypatch):
    improver = _make_improver()
    import self_improver as si_mod

    monkeypatch.setattr(si_mod, "_subprocess_run", lambda *a, **k: ("x",))
    result = improver.code_run("f = open('r.txt')\nf.write('x')")
    assert "error" in result
    assert "blocked" in result["error"]


def test_code_run_allows_read_only(monkeypatch):
    improver = _make_improver()
    import self_improver as si_mod

    captured = {}

    class _FakeProc:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(si_mod, "_subprocess_run", _fake_run)
    # Guard passes; subprocess is reached. We don't care about real output,
    # only that no "blocked" error is returned.
    result = improver.code_run("print(2 + 2)")
    assert "error" not in result or "blocked" not in result.get("error", "")
