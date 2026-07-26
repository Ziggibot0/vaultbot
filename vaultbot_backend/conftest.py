"""Pytest harness for VaultBot backend tests.

This conftest makes leaf modules importable WITHOUT importing main.py
(main.py calls acquire_lock() → sys.exit + loads a 4176-vector FAISS
index at import time; importing it in a test process would either
sys.exit the pytest run or load the live index).

A pytest_collection_modify_items guard hard-fences `import main`: if any
test imported `main`, the run FAILS with a clear message before any test
body executes. This prevents a future test author from accidentally
triggering the PID lock.

Documentation grounding:
- pytest_collection_modifyitems: "Called after collection has been
  performed. May filter or re-order the items in-place."
  https://docs.pytest.org/en/stable/reference/reference.html
- testpaths ini option: "Directories to search for tests when no files
  or directories are given on the command line."
- tmp_path + monkeypatch fixtures auto-clean after each test.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.resolve()
# Make leaf modules (fused_retrieval, abstract_context, self_improver,
# embedding_drift, free_search, custom_tools.textbook_ingest) importable
# WITHOUT touching main.py.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def pytest_collection_modifyitems(items):
    """Hard-fence: FAIL the run if any test imported `main`.

    main.py's module top-level calls acquire_lock() which sys.exit(0)s
    if a backend is already running (vaultbot.pid exists). Importing it
    in a test process is never safe. We scan sys.modules AFTER
    collection (so all imports have resolved) and fail any item whose
    module graph pulled in `main`.
    """
    if "main" in sys.modules:
        # Inject a fail mark on every item so the run reports a clear
        # error instead of silently running against a poisoned state.
        import pytest
        for item in items:
            item.add_marker(pytest.mark.fail(
                reason="A test imported `main` (forbidden — it calls "
                       "acquire_lock() → sys.exit + loads the live FAISS "
                       "index). Import leaf modules only. See "
                       "conftest.py docstring."
            ))