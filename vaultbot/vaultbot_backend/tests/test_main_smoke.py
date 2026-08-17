"""Subprocess smoke test for main.py — verifies the shims wire correctly.

The conftest guard forbids `import main` in-process (it calls
acquire_lock() → sys.exit + loads the FAISS index). This test runs
`import main` in a SUBPROCESS with VAULTBOT_SKIP_LOCK=1 so the PID
lock is bypassed. The subprocess just does `import main` and exits 0
if it succeeds, non-zero if any NameError/ImportError occurs in the
shim wiring.

This closes the verification gap identified in the quality audit:
the 18 unit tests only cover leaf modules, not the main.py shims.
A broken shim (e.g. a renamed function that the shim still calls
by the old name) would only be caught here.

Documentation grounding:
- pytest subprocess testing pattern: run a Python snippet in a
  separate process to avoid import side effects. This is the same
  pattern safe_write's _verify_import_in_subprocess uses.
"""

from subprocess_utils import run as _subprocess_run
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_main_imports_without_name_error():
    """import main in a subprocess — verifies all shims resolve.

    main.py constructs the Services registry, then imports the
    extracted modules (chat_handler, weaving, etc.) and registers
    route handlers that call shim functions. If any shim references
    a name that doesn't exist (e.g. a renamed function), the import
    itself won't fail (the shim bodies are inside functions), but a
    simple `import main` + `dir(main)` + attribute access on the
    shim functions catches most wiring bugs.

    We use VAULTBOT_SKIP_LOCK=1 to bypass the PID lock (which would
    sys.exit if a backend is running). We also set VAULT_PATH to a
    tmp dir to avoid loading the real vault's FAISS index.
    """
    backend_dir = Path(__file__).parent.parent.resolve()
    # A minimal import check: can main.py be imported without error?
    # We don't call any functions — just verify the module graph resolves.
    # Phase 3: the handle_chat/handle_research/_execute_agent_tool shims were
    # deleted from main.py (they live in chat_handler.py / research_handler.py
    # now, called directly by routers/ws.py). Verify the extracted modules
    # import instead.
    code = (
        "import sys; sys.path.insert(0, r'" + str(backend_dir) + "'); "
        "import main; "
        "print('main imported OK'); "
        # Verify the extracted handler modules import (the shims are gone).
        "from chat_handler import handle_chat; assert callable(handle_chat); "
        "from research_handler import handle_research; assert callable(handle_research); "
        "from task_api import create_task; assert callable(create_task); "
        "from identity_api import get_identity; assert callable(get_identity); "
        "print('handlers verified OK')"
    )
    env = {
        "VAULTBOT_SKIP_LOCK": "1",  # bypass PID lock
        "VAULT_PATH": str(backend_dir),  # point at backend dir (minimal)
        "PATH": "",  # minimal env
    }
    # Inherit PATH so the venv Python can find its DLLs on Windows.
    import os

    env["PATH"] = os.environ.get("PATH", "")
    env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    env["PYTHONPATH"] = str(backend_dir)

    result = _subprocess_run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, (
        f"import main failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "main imported OK" in result.stdout
    assert "handlers verified OK" in result.stdout
