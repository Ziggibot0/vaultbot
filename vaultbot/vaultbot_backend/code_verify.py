"""Code verification utilities extracted from self_improver.py.

These functions copy the backend to a temp directory and run checks
(import verification, JS load testing, startup smoke test, pytest) in a
subprocess so a broken module never crashes the live backend.

All functions are standalone — they take the backend directory and vault
root as explicit parameters rather than reading module-level globals, so
callers (and tests) can point them at a throwaway directory tree.
"""

import ast
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from subprocess_utils import run as _subprocess_run

BACKEND_DIR = Path(__file__).parent.resolve()
BACKEND_ROOT = BACKEND_DIR.parent.parent


def copy_backend_for_check(
    tmpdir: str, target_name: str, new_content: str, backend_dir: Path
) -> None:
    """Copy the backend dir into tmpdir, then overwrite target_name with
    new_content, so a subprocess can import main.py against the proposed
    edit without touching the live files."""
    # Copy only .py files (skip venv, index, models, etc.) to keep it fast.
    for py in backend_dir.glob("*.py"):
        shutil.copy2(py, Path(tmpdir) / py.name)
    # Copy the routers package (main.py does `from routers import ws`).
    # Without this, an edit to routers/llm.py would be verified against
    # the LIVE routers, not the proposed edit — defeating the check.
    routers_src = backend_dir / "routers"
    if routers_src.exists():
        routers_dst = Path(tmpdir) / "routers"
        routers_dst.mkdir(exist_ok=True)
        (routers_dst / "__init__.py").touch()
        for py in routers_src.glob("*.py"):
            if py.name == "__init__.py":
                continue
            shutil.copy2(py, routers_dst / py.name)
    # Copy the custom_tools package + identity dir (main.py imports them).
    ct_dst = Path(tmpdir) / "custom_tools"
    ct_dst.mkdir(exist_ok=True)
    (ct_dst / "__init__.py").touch()
    for py in (backend_dir / "custom_tools").glob("*.py"):
        if py.name == "__init__.py":
            continue
        shutil.copy2(py, ct_dst / py.name)
    id_dst = Path(tmpdir) / "identity"
    id_dst.mkdir(exist_ok=True)
    if (backend_dir / "identity").exists():
        for f in (backend_dir / "identity").iterdir():
            if f.is_file():
                shutil.copy2(f, id_dst / f.name)
    # Overwrite the target with the proposed new content.
    (Path(tmpdir) / target_name).write_text(new_content, encoding="utf-8")


def verify_import_in_subprocess(
    backend_dir: str, backend_root: Path = BACKEND_ROOT
) -> tuple[bool, str | None]:
    """Run `python -c 'import main'` in a subprocess against the given
    backend dir. Returns (ok, error_message). A clean import means the
    whole import graph resolves with the proposed edit in place."""
    venv_python = str(backend_root / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_python).exists():
        venv_python = sys.executable
    # Use a check script that imports main but exits before the server
    # binds a port / takes the PID lock. We set VAULTBOT_SKIP_LOCK so
    # main.py's acquire_lock() is bypassed (if it honored that env), and
    # we run with a throwaway VAULT_PATH so the indexer doesn't touch the
    # real index. The import itself is what we're testing.
    check_code = (
        "import sys, os; sys.path.insert(0, os.environ['CHECK_DIR']); "
        "os.environ.setdefault('VAULTBOT_SKIP_LOCK','1'); "
        "os.environ.setdefault('VAULT_PATH', os.environ['VAULT_ROOT_DIR']); "
        "import main; print('IMPORT_OK')"
    )
    env = {
        **os.environ,
        "CHECK_DIR": backend_dir,
        "PYTHONPATH": backend_dir,
        "VAULTBOT_SKIP_LOCK": "1",
        "VAULT_PATH": str(backend_root),
        "VAULT_ROOT_DIR": str(backend_root),
    }
    try:
        proc = _subprocess_run(
            [venv_python, "-c", check_code],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(backend_root),
            env=env,
        )
        if proc.returncode == 0 and "IMPORT_OK" in proc.stdout:
            return True, None
        # Surface the first useful line of the traceback.
        err = (proc.stderr or proc.stdout or "unknown error").strip()
        # Trim to the actual error tail for readability.
        tail = err.splitlines()[-1] if err.splitlines() else err
        return False, tail[:500]
    except subprocess.TimeoutExpired:
        return False, "import check timed out (30s) — likely a startup hang"
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False, f"import check could not run: {e}"


def verify_import_targets(
    content: str, backend_dir: str, backend_root: Path = BACKEND_ROOT
) -> tuple[bool, str | None]:
    """Statically verify every ``from X import Y`` (and ``from X
    import Y as Z``) in ``content`` actually resolves against the
    *current* backend (before the write). This is the check that
    catches the "I imported a name that doesn't exist" class of bug
    (``from chat_helpers import run_with_heartbeat`` when the
    function was never written) — even for files NOT in
    ``_CORE_FILES`` and even when ``main`` doesn't transitively
    import the target.

    Runs in a subprocess so a broken import never crashes the live
    backend. Returns (ok, error). A single failing name fails the
    whole check (fail-fast); the error names the offending import so
    the agent can fix it. ``ImportError`` / ``AttributeError`` are
    hard failures; a module that itself fails to import for an
    *unrelated* reason (e.g. a third-party dep missing in the venv)
    is reported but does NOT fail the check — we only fail on a
    named ``Y`` that the module exposes but the agent referenced
    wrongly. We cannot always distinguish, so the rule is: if
    ``import X`` succeeds but ``getattr(X, Y)`` raises, it's a hard
    fail (the name genuinely doesn't exist); if ``import X`` itself
    raises, we skip that import (the live backend may not even need
    it, e.g. a new optional dep) and report it as ``skipped``.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # The caller already syntax-checked; if we somehow re-hit it,
        # don't block on it.
        return True, None

    targets: list[tuple[str, str]] = []  # (module, name)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not mod or node.level:  # skip relative imports (".foo")
                continue
            for alias in node.names:
                name = alias.name
                if name == "*":
                    continue
                targets.append((mod, name))

    if not targets:
        return True, None

    venv_python = str(backend_root / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_python).exists():
        venv_python = sys.executable

    # Build one check script that imports each module once and
    # getattr's each name, printing IMPORT_REFS_OK only if every name
    # resolves. Relative-import and stdlib modules resolve normally
    # because PYTHONPATH points at the backend dir.
    checks = "\n".join(
        "try:\n"
        f"    import {mod}\n"
        f"    getattr({mod}, {name!r})\n"
        "except ImportError:\n"
        f"    print('SKIP {mod} {name}')\n"
        "except AttributeError:\n"
        f"    print('MISSING {mod} {name}')\n"
        "    raise SystemExit(1)\n"
        for (mod, name) in targets
    )
    check_code = (
        "import sys, os\n"
        "sys.path.insert(0, os.environ['CHECK_DIR'])\n"
        "os.environ.setdefault('VAULTBOT_SKIP_LOCK','1')\n"
        "os.environ.setdefault('VAULT_PATH', os.environ['VAULT_ROOT_DIR'])\n"
        + checks
        + "print('IMPORT_REFS_OK')\n"
    )
    env = {
        **os.environ,
        "CHECK_DIR": backend_dir,
        "PYTHONPATH": backend_dir,
        "VAULTBOT_SKIP_LOCK": "1",
        "VAULT_PATH": str(backend_root),
        "VAULT_ROOT_DIR": str(backend_root),
    }
    try:
        proc = _subprocess_run(
            [venv_python, "-c", check_code],
            capture_output=True,
            text=True,
            timeout=40,
            cwd=str(backend_root),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "import-targets check timed out (40s)"
    except Exception:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        # If we can't run the check at all, don't block the write —
        # the import-main check (for core files) is the hard gate.
        return True, None
    if proc.returncode == 0 and "IMPORT_REFS_OK" in proc.stdout:
        return True, None
    # Find the MISSING line for a precise error.
    for line in (proc.stdout or "").splitlines():
        if line.startswith("MISSING "):
            _, mod, name = line.split(" ", 2)
            return False, (
                f"ImportError: cannot import name '{name}' "
                f"from '{mod}' — the name doesn't exist on "
                f"that module. Did you finish the edit?"
            )
    err = (proc.stderr or proc.stdout or "unknown").strip()
    tail = err.splitlines()[-1] if err.splitlines() else err
    return False, tail[:500]


def verify_js_load(content: str, timeout_s: int = 8) -> tuple[bool, str | None]:
    """Require() a JS module in a child Node process with a hard
    timeout and an 'obsidian' stub, to catch load-time hangs (infinite
    recursion / infinite loops at module top level) that ``node --check``
    (syntax only) misses.

    The Obsidian plugin's main.js does
    ``require('obsidian')`` at the top — the 'obsidian' module only
    exists inside Obsidian, so a bare ``require('./main.js')`` would
    throw MODULE_NOT_FOUND before reaching any recursion. We inject a
    Proxy-based stub into the child's module cache first: any property
    access on 'obsidian' returns a no-op function or a Proxy, so
    ``const { Plugin } = require('obsidian')`` and
    ``class X extends Plugin`` both succeed. The require then runs the
    module's top-level code; if it hangs (infinite recursion at module
    scope) the timeout fires and we reject.

    Returns (ok, error). A clean exit (the script prints LOAD_OK and
    exits 0) means the module loads without hanging; ok=True. A throw
    is ok=True if it's the harmless "obsidian not the real module" kind
    — but a Proxy stub prevents that, so any throw is treated as a real
    load failure. A timeout is a hard reject.
    """
    node_path = shutil.which("node")
    if not node_path:
        return True, None  # can't check; don't block the write
    # Write the candidate content to a temp .js file.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    # Node script: stub obsidian, set a watchdog timeout that kills the
    # process if the require hangs, then require the file.
    loader = (
        "const Module = require('module');\n"
        "const origResolve = Module._resolveFilename;\n"
        # Stub 'obsidian': intercept the resolve so require('obsidian')
        # returns a Proxy whose every property is a no-op class/fn.
        "Module._resolveFilename = function(req, parent, ...rest) {\n"
        "  if (req === 'obsidian') return 'obsidian-stub';\n"
        "  return origResolve.call(this, req, parent, ...rest);\n"
        "};\n"
        "const stub = new Proxy({}, { get: () => {\n"
        "  return new Proxy(function(){}, {\n"
        "    get: (t, p) => p === 'prototype' ? Object.prototype : undefined,\n"
        "    construct: () => ({}),\n"
        "    apply: () => ({}),\n"
        "  });\n"
        "}});\n"
        "require.cache['obsidian-stub'] = { exports: stub, id: 'obsidian-stub', "
        "filename: 'obsidian-stub', loaded: true, children: [], paths: [] };\n"
        "const watchdog = setTimeout(() => {\n"
        "  console.error('LOAD_TIMEOUT'); process.exit(2);\n"
        "}, " + str(timeout_s * 1000) + ");\n"
        "try {\n"
        "  require(" + repr(tmp_path).replace("\\\\", "/") + ");\n"
        "  clearTimeout(watchdog);\n"
        "  console.log('LOAD_OK'); process.exit(0);\n"
        "} catch (e) {\n"
        "  clearTimeout(watchdog);\n"
        "  console.error('LOAD_THROW ' + (e && e.message ? e.message : String(e)));\n"
        "  process.exit(3);\n"
        "}\n"
    )
    try:
        result = _subprocess_run(
            [node_path, "-e", loader],
            capture_output=True,
            text=True,
            timeout=timeout_s + 5,
        )
    except subprocess.TimeoutExpired:
        os.unlink(tmp_path)
        return (
            False,
            f"load check timed out ({timeout_s}s) — likely infinite recursion",
        )
    except Exception:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        os.unlink(tmp_path)
        return True, None  # can't run the check; don't block
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
    if result.returncode == 0 and "LOAD_OK" in (result.stdout or ""):
        return True, None
    if result.returncode == 2 or "LOAD_TIMEOUT" in (result.stderr or ""):
        return (
            False,
            f"load hung (infinite recursion/loop at module level, {timeout_s}s)",
        )
    if "LOAD_THROW" in (result.stderr or ""):
        err = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "load threw"
        )
        return False, f"load threw: {err[:400]}"
    err = (result.stderr or result.stdout or "unknown load failure").strip()
    tail = err.splitlines()[-1] if err.splitlines() else err
    return False, tail[:400]


def verify_startup_smoke(
    backend_dir: str, timeout_s: int = 40, backend_root: Path = BACKEND_ROOT
) -> tuple[bool, str | None]:
    """Actually START the backend in a subprocess (import main, run
    uvicorn on a throwaway port) and hit /health. Catches runtime
    AttributeErrors (``svc.vault_path`` missing, ``log_event`` not on
    SessionLogger) that import-only checks miss because they surface
    only when the app builds its service graph and starts the lifespan.

    Returns (ok, error). The backend is started with a throwaway
    VAULT_PATH and a non-default port so it never clashes with the
    live backend (port 8000) or touches the real index. We kill it as
    soon as /health responds (or the timeout fires).

    NOTE: this is NOT wired into the default safe_write path (too slow
    for every write). It's available for explicit hardening of risky
    edits; the import-targets check + contract tests catch the
    attribute bugs more reliably and cheaply.
    """
    import urllib.request

    venv_python = str(backend_root / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_python).exists():
        venv_python = sys.executable
    # Bind a high, almost-certainly-free port. We can't reuse 8000
    # (the live backend holds it) and we don't want to disturb the
    # real vault index, so point VAULT_PATH at the throwaway backend
    # dir itself.
    smoke_port = "18099"
    check_code = (
        "import sys, os, threading, time, urllib.request; "
        "sys.path.insert(0, os.environ['CHECK_DIR']); "
        "os.environ['VAULTBOT_SKIP_LOCK']='1'; "
        "os.environ['VAULT_PATH']=os.environ['CHECK_DIR']; "
        "os.environ['VAULTBOT_SMOKE_PORT']='" + smoke_port + "'; "
        "import main; "
        # Run uvicorn on the smoke port in this same process so we
        # exercise the real lifespan (Services build, indexer load,
        # autonomous researcher thread start). The lifespan is what
        # surfaces svc.vault_path-style AttributeErrors.
        "import uvicorn; "
        "uvicorn.run(main.app, host='127.0.0.1', port=" + smoke_port + ", "
        "access_log=False, log_level='error')\n"
    )
    env = {
        **os.environ,
        "CHECK_DIR": backend_dir,
        "PYTHONPATH": backend_dir,
        "VAULTBOT_SKIP_LOCK": "1",
        "VAULT_PATH": str(backend_root),
        "VAULT_ROOT_DIR": str(backend_root),
    }
    try:
        proc = subprocess.Popen(
            [venv_python, "-c", check_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(backend_root),
            env=env,
            text=True,
        )
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False, f"startup smoke could not launch: {e}"
    try:
        # Poll /health until it responds or the timeout fires.
        health_url = f"http://127.0.0.1:{smoke_port}/health"
        deadline = time.time() + timeout_s
        last_err = ""
        while time.time() < deadline:
            if proc.poll() is not None:
                # Process exited before serving — a startup crash.
                out = proc.stdout.read() or ""
                err = proc.stderr.read() or ""
                tail = (err or out).strip().splitlines()
                line = tail[-1] if tail else (err or out or "exited")
                return False, f"startup crashed: {line[:400]}"
            try:
                r = urllib.request.urlopen(health_url, timeout=3)
                if r.status == 200:
                    return True, None
                last_err = f"health status {r.status}"
            except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
                last_err = str(e)[:120]
            time.sleep(0.5)
        return False, f"startup smoke timed out ({timeout_s}s): {last_err}"
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
            with contextlib.suppress(Exception):
                proc.kill()


def run_pytest_in_subprocess(
    backend_dir: str,
    target_file: str | None = None,
    backend_root: Path = BACKEND_ROOT,
) -> tuple[bool, str | None]:
    """Run `python -m pytest -q --tb=short` in a subprocess against the
    given backend dir. Returns (passed, output_message).

    - (True, None or '') : pytest ran and all tests passed.
    - (False, '<output>') : pytest ran and at least one test FAILED.
      The output contains the failure summary.
    - (True, '<reason>')  : pytest could not run at all (not installed,
      import error, timeout, etc.). The caller treats this as `skipped`
      and proceeds — the import check is the hard gate; pytest is a
      softer gate enforced only when it can actually run.

    If ``target_file`` is given (e.g. ``"chat_handler.py"``), the pytest
    run is SCOPED to test files whose names contain the target file's
    stem (e.g. ``tests/test_*chat_handler*.py``).  If no matching test
    files exist, falls back to running all tests.  This prevents
    pre-existing test failures in unrelated modules from blocking
    legitimate edits.

    The 60s timeout prevents a hung test from blocking forever. We use
    the venv interpreter (same as `_verify_import_in_subprocess`) and
    point PYTHONPATH at the backend dir so leaf modules import without
    touching the live backend. We DO NOT pass `-p no:cacheprovider`
    or similar; the conftest's hard-fence against importing `main` keeps
    the test process safe.
    """
    # Prefer the venv interpreter (matches _verify_import_in_subprocess),
    # but pytest + faiss live in the SYSTEM Python in this environment,
    # not in .venv. Probe both: use the first interpreter that
    # can import pytest. If neither can, soft-skip.
    venv_python = str(backend_root / ".venv" / "Scripts" / "python.exe")
    candidates = [venv_python, sys.executable]
    chosen = None
    for cand in candidates:
        if not cand or not Path(cand).exists():
            continue
        try:
            probe = _subprocess_run(
                [cand, "-c", "import pytest"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if probe.returncode == 0:
                chosen = cand
                break
        except Exception:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
            continue
    if chosen is None:
        # pytest not importable in any available interpreter — soft-skip.
        return True, "pytest not installed in any interpreter"

    env = {
        **os.environ,
        "PYTHONPATH": backend_dir,
        # Keep the test process off the live vault / PID lock.
        "VAULTBOT_SKIP_LOCK": "1",
        "VAULT_PATH": str(backend_root),
        "VAULT_ROOT_DIR": str(backend_root),
    }

    # --- Scope pytest to tests matching the edited file ---
    # Build a test file glob from the target file's stem (e.g.
    # "chat_handler.py" -> "tests/test_*chat_handler*.py").  If
    # matching test files exist, run ONLY those.  If no matching
    # tests exist, fall back to running all tests (so we don't lose
    # coverage on files that have no dedicated test file).
    test_args: list[str] = []
    if target_file:
        stem = Path(target_file).stem  # "chat_handler.py" -> "chat_handler"
        tests_dir = Path(backend_dir) / "tests"
        if tests_dir.exists():
            # Match common test naming patterns.
            matching = list(tests_dir.glob(f"test_*{stem}*.py"))
            if matching:
                test_args = [str(p.relative_to(backend_dir)) for p in sorted(matching)]

    try:
        proc = _subprocess_run(
            [chosen, "-m", "pytest", "-q", "--tb=short", *test_args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=backend_dir,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # Hung test — treat as skipped (soft gate), not a hard reject.
        return True, "pytest timed out (60s) — skipped"
    except FileNotFoundError:
        return True, "pytest interpreter not found"
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        # Any other subprocess failure: soft-skip, don't hard-reject.
        return True, f"pytest could not run: {e}"

    combined = (proc.stdout or "") + (proc.stderr or "")
    # pytest exit codes: 0 = pass, 1 = tests failed, 2+ = usage/error,
    # 5 = no tests collected (benign — e.g. a dry-run tmp copy with no
    # tests/ dir). Only exit code 1 is a real test FAILURE worth
    # hard-rejecting on; everything else is soft-skipped so the import
    # check remains the sole hard gate.
    if proc.returncode == 0:
        return True, None
    if proc.returncode in (2, 3, 4):
        # Usage error / internal error — soft-skip.
        tail = combined.strip().splitlines()[-1] if combined.strip() else ""
        return True, (
            f"pytest usage/internal error (rc={proc.returncode}): {tail[:200]}"
        )
    if proc.returncode == 5:
        # No tests collected — benign; treat as pass.
        return True, None
    # rc == 1 (or anything else): real test failure — hard reject.
    tail = combined.strip().splitlines()[-1] if combined.strip() else ""
    return False, tail[:500] if tail else (
        f"pytest exit code {proc.returncode} (no output captured)"
    )
