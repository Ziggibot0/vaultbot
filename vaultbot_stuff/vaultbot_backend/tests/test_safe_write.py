"""Tests for SelfImprover.safe_write — the hardened self-edit path.

These tests verify the two safety properties that matter for an agent that
edits its own source code:

  1. **Syntax gate**: invalid Python is rejected before any disk touch.
  2. **Import-graph gate**: an edit that would break the backend entry
     point (verified by importing it in a subprocess) is rejected and,
     if already written, auto-rolled-back from the `.bak`.

CRITICAL SAFETY PROPERTY — keeping writes off the live backend
-----------------------------------------------------------------
`safe_write`, `_resolve_path`, `_copy_backend_for_check`, and
`_verify_import_in_subprocess` all read module-level globals
(`BACKEND_ROOT`, `BACKEND_DIR`, `CUSTOM_TOOLS_DIR`) defined at the top of
`self_improver.py`. To guarantee that NO test ever mutates a real backend
file, the `patched_improver` fixture monkeypatches ALL THREE globals
*before* constructing the `SelfImprover` instance:

  - ``self_improver.BACKEND_ROOT``  -> ``tmp_path``            (acts as vault root)
  - ``self_improver.BACKEND_DIR``   -> ``tmp_path / "vaultbot_backend"``
  - ``self_improver.CUSTOM_TOOLS_DIR`` -> ``tmp_path / "vaultbot_backend" / "custom_tools"``

Because `_resolve_path` resolves every path relative to `BACKEND_ROOT`,
every target file lands under ``tmp_path``. Because `is_core` compares
``full.parent.resolve() == BACKEND_DIR``, core-file detection still works
against the temp tree. `monkeypatch` auto-undoes every patch at test
teardown (https://docs.pytest.org/en/stable/reference/reference.html), so
the live globals are restored for any later test. `tmp_path` is a unique
per-test ``pathlib.Path`` (https://docs.pytest.org/en/stable/reference/
reference.html) that pytest cleans up automatically.

Only leaf modules are imported — never `main` (see conftest.py hard-fence).
Test anatomy follows https://docs.pytest.org/en/stable/explanation/anatomy.html
(arrange / act / assert).
"""

import hashlib

import pytest
import self_improver

# The *real* backend dir, captured once at import time (before any
# monkeypatching) so we can copy a genuine core file into the temp tree
# when a test needs one. Reading is safe; we never write here.
_REAL_BACKEND_DIR = self_improver.BACKEND_DIR


@pytest.fixture
def patched_improver(tmp_path, monkeypatch):
    """A SelfImprover whose BACKEND_DIR / BACKEND_ROOT / CUSTOM_TOOLS_DIR
    all point at a throwaway tmp tree, so safe_write can never reach the
    live backend.

    Layout created under tmp_path (mimicking the real repo so _resolve_path
    and is_core both behave naturally)::

        tmp_path/                          # == BACKEND_ROOT
          vaultbot_backend/                # == BACKEND_DIR
            custom_tools/__init__.py       # empty package -> load_custom_tools is a no-op
    """
    backend_dir = tmp_path / "vaultbot_backend"
    backend_dir.mkdir()
    custom_tools = backend_dir / "custom_tools"
    custom_tools.mkdir()
    (custom_tools / "__init__.py").write_text(
        "# VaultBot custom tools (agent-authored)\n", encoding="utf-8"
    )

    # Patch the module globals that safe_write / _resolve_path /
    # _copy_backend_for_check / __init__ all read. Order matters: these
    # must be set BEFORE SelfImprover() is constructed, because __init__
    # does CUSTOM_TOOLS_DIR.mkdir(...) and load_custom_tools().
    monkeypatch.setattr(self_improver, "BACKEND_ROOT", tmp_path, raising=True)
    monkeypatch.setattr(self_improver, "BACKEND_DIR", backend_dir, raising=True)
    monkeypatch.setattr(self_improver, "CUSTOM_TOOLS_DIR", custom_tools, raising=True)

    improver = self_improver.SelfImprover()
    return improver, backend_dir


# ---------------------------------------------------------------------------
# Test 1 — syntax gate (no disk touch)
# ---------------------------------------------------------------------------
def test_rejects_syntax_error(patched_improver, tmp_path):
    """A SyntaxError is caught by ast.parse before any file is written.

    Arrange: SelfImprover with BACKEND_DIR -> tmp_path.
    Act:     safe_write("vaultbot_backend/fused_retrieval.py", "def (")
    Assert:  status == "rejected", error mentions SyntaxError, and no
             file was created on disk.
    """
    improver, backend_dir = patched_improver

    # "def (" is invalid Python -> ast.parse raises SyntaxError.
    result = improver.safe_write("vaultbot_backend/fused_retrieval.py", "def (")

    assert result["status"] == "rejected"
    assert "SyntaxError" in result["error"]
    # The syntax check is the very first gate; nothing should be on disk.
    assert "syntax" in result["checks"]
    assert result["checks"]["syntax"].startswith("FAIL")
    # Critical: no file was written.
    assert not (backend_dir / "fused_retrieval.py").exists()


# ---------------------------------------------------------------------------
# Test 2 — dry run of a breaking core edit leaves disk untouched
# ---------------------------------------------------------------------------
def test_dry_run_rejects_breaking_edit_without_touching_disk(
    patched_improver, tmp_path, monkeypatch
):
    """A dry_run that WOULD break the backend is reported as
    dry_run_rejected, and the on-disk file is byte-for-byte unchanged.

    Arrange: copy the real abstract_context.py into the tmp backend dir so
    it is a present core file; simulate the subprocess import failure so we
    don't spawn a real interpreter against an incomplete tmp backend.
    Act:     safe_write("abstract_context.py" path, "# empty file\\n",
             dry_run=True)
    Assert:  status == "dry_run_rejected", would_break_backend == True, and
             the file on disk is identical (same sha256) to before.
    """
    improver, backend_dir = patched_improver

    # Stand-in core file: copy the genuine abstract_context.py into the
    # temp backend dir. (Read-only copy of a real file; we never write back.)
    src = _REAL_BACKEND_DIR / "abstract_context.py"
    dst = backend_dir / "abstract_context.py"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    original_sha = hashlib.sha256(dst.read_bytes()).hexdigest()

    # Simulate the subprocess import check failing, so the test is
    # deterministic and doesn't rely on a real interpreter/main.py in the
    # tmp tree. safe_write calls self._verify_import_in_subprocess in the
    # dry_run core branch.
    monkeypatch.setattr(
        improver,
        "_verify_import_in_subprocess",
        lambda backend_dir: (
            False,
            "simulated ImportError: cannot import "
            "name build_abstract_context from main",
        ),
        raising=True,
    )

    # Valid Python syntax but guts the module -> would break a caller.
    result = improver.safe_write(
        "vaultbot_backend/abstract_context.py", "# empty file\n", dry_run=True
    )

    assert result["status"] == "dry_run_rejected"
    assert result["would_break_backend"] is True
    assert "FAIL" in result["checks"]["import_check"]

    # Critical safety property: dry_run must NOT mutate the on-disk file.
    after_sha = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert after_sha == original_sha, (
        "dry_run mutated the on-disk file — it must be read-only!"
    )


# ---------------------------------------------------------------------------
# Test 3 — real write to a core file auto-rolls-back on import failure
# ---------------------------------------------------------------------------
def test_real_write_auto_rolls_back_on_import_failure(
    patched_improver, tmp_path, monkeypatch
):
    """A real (non-dry-run) write to a core file that fails the import
    check must be auto-rolled-back from the .bak, leaving the original
    content intact on disk.

    Arrange: create a minimal lazy_condenser.py (a core file) in the tmp
    backend dir with a real function; simulate import failure.
    Act:     safe_write("...lazy_condenser.py", "x = 1\\n")  # valid syntax,
             removes the function a caller imports.
    Assert:  status == "rejected", "auto_rollback" in checks, and the file
             on disk equals the ORIGINAL content (restored from .bak).
    """
    improver, backend_dir = patched_improver

    original_content = (
        '"""Lazy condenser module."""\ndef condense(text):\n    return text[:100]\n'
    )
    core_file = backend_dir / "lazy_condenser.py"
    core_file.write_text(original_content, encoding="utf-8")
    assert "lazy_condenser.py" in self_improver.SelfImprover._CORE_FILES

    # Simulate the post-write import check failing so the rollback path
    # fires deterministically (no real interpreter / main.py needed).
    monkeypatch.setattr(
        improver,
        "_verify_import_in_subprocess",
        lambda backend_dir: (
            False,
            "simulated ImportError: cannot import name condense from lazy_condenser",
        ),
        raising=True,
    )

    result = improver.safe_write("vaultbot_backend/lazy_condenser.py", "x = 1\n")

    assert result["status"] == "rejected"
    assert "auto_rollback" in result["checks"], (
        "expected auto-rollback to fire on import failure"
    )
    assert "restored from .bak" in result["checks"]["auto_rollback"]

    # Critical: the original content must be restored on disk.
    assert core_file.read_text(encoding="utf-8") == original_content, (
        "auto-rollback did not restore the original file content"
    )


# ---------------------------------------------------------------------------
# Test 4 — non-core file skips the import check and writes cleanly
# ---------------------------------------------------------------------------
def test_non_core_file_skips_import_check(patched_improver, tmp_path):
    """A write to a file NOT in _CORE_FILES skips the subprocess import
    check and is written directly.

    Arrange: BACKEND_DIR -> tmp_path; SelfImprover.
    Act:     safe_write("...my_new_tool.py", "def run(args): return {}\\n")
    Assert:  status == "written", import_check is absent (skipped for
             non-core), and the file exists on disk with the exact content.
    """
    improver, backend_dir = patched_improver

    content = "def run(args): return {}\n"
    result = improver.safe_write("vaultbot_backend/my_new_tool.py", content)

    assert result["status"] == "written"
    assert result["is_core"] is False
    # Non-core path never sets checks["import_check"].
    assert "import_check" not in result["checks"]
    assert result["checks"]["syntax"] == "ok"

    written = backend_dir / "my_new_tool.py"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == content
