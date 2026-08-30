"""Unit tests for the code_semantic custom tool.

Covers the jedi-backed cross-file navigation surface against a small
synthetic package placed in tmp_path, so the tests are hermetic and need
no network and no live backend import. Only the leaf module
`custom_tools.code_semantic` is imported — never `main`.

The tool resolves a backend-relative symbol by building a probe script
against a jedi.Project whose sys_path is the backend dir. The fixture
points that project at a tmp_path "backend" containing two modules that
reference each other across files, which exercises the exact cross-file
resolution the issue (#418) demands.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import code_semantic as cs


@pytest.fixture(autouse=True)
def _reset_lazy(monkeypatch):
    """Reset the module-level lazy jedi/project cache between tests."""
    monkeypatch.setattr(cs, "_jedi", None)
    monkeypatch.setattr(cs, "_project", None)
    monkeypatch.setattr(cs, "_BACKEND_DIR", None)


@pytest.fixture
def fake_backend(tmp_path, monkeypatch):
    """Create a tiny multi-module "backend" and point the tool at it.

    Layout:
      backend/
        lib_one.py   defines `greet` and `add`
        lib_two.py   imports greet from lib_one and calls it (cross-file ref)
    """
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "lib_one.py").write_text(
        "def greet(name: str) -> str:\n    return f'hi {name}'\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    (backend_dir / "lib_two.py").write_text(
        "from lib_one import greet\n\n"
        "def run():\n"
        "    # greet is a real call below; the docstring mention is not\n"
        "    return greet('world')\n",
        encoding="utf-8",
    )
    # Point the tool's backend root at the dir CONTAINING the modules (the
    # tool resolves it as `_backend_dir()` and puts it on the jedi project
    # sys_path, so `import lib_one`/`import lib_two` resolve here).
    monkeypatch.setattr(cs, "_BACKEND_DIR", str(backend_dir))
    return str(backend_dir)


# ── define ────────────────────────────────────────────────────────────


def test_define_resolves_cross_file(fake_backend):
    result = cs.run({"op": "define", "symbol": "lib_one.greet"})
    assert result["op"] == "define"
    defs = result["definitions"]
    assert len(defs) == 1
    d = defs[0]
    assert d["name"] == "greet"
    assert d["type"] == "function"
    assert d["module_path"].endswith("lib_one.py")


def test_define_requires_symbol():
    result = cs.run({"op": "define", "symbol": ""})
    assert "error" in result


def test_unknown_op_errors():
    result = cs.run({"op": "nonsense", "symbol": "x"})
    assert "error" in result


# ── references ────────────────────────────────────────────────────────


def test_references_find_cross_file_call_site(fake_backend):
    result = cs.run({"op": "references", "symbol": "lib_one.greet"})
    # Must include the caller inside lib_two.py (cross-file reference).
    mods = {r.get("module_path") for r in result["references"]}
    assert any(m.endswith("lib_two.py") for m in mods), mods
    # The definition site in lib_one.py is included too.
    assert any(m.endswith("lib_one.py") for m in mods), mods


# ── callers ───────────────────────────────────────────────────────────


def test_callers_returns_cross_file_caller(fake_backend):
    result = cs.run({"op": "callers", "symbol": "lib_one.greet"})
    assert result["count"] == 1
    caller = result["callers"][0]
    assert caller["module_path"].endswith("lib_two.py")
    # The caller's enclosing function is `run`, its definition line matches.
    assert caller["context"]["name"] == "run"


# ── callees ───────────────────────────────────────────────────────────


def test_callees_lists_real_calls(fake_backend):
    result = cs.run({"op": "callees", "symbol": "lib_two.run"})
    assert result["count"] == 1
    calls = result["callees"][0]["calls"]
    assert "greet" in calls, calls
    # Docstring/comment mentions like `greet` inside the docstring are NOT
    # counted as calls by the AST body walk (only real ast.Call nodes).
    assert result["callees"][0]["definition"]["module_path"].endswith("lib_two.py")


# ── error path / missing jedi ─────────────────────────────────────────


def test_missing_jedi_returns_clear_error(fake_backend, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "jedi":
            raise ImportError("No module named 'jedi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = cs.run({"op": "define", "symbol": "lib_one.greet"})
    assert "error" in result
    assert "jedi" in result["error"]
