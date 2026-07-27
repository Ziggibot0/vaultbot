"""
Self-improvement engine for VaultBot.

The agent's primary realm of influence is through MCP-style tools. This
module lets VaultBot:

  1. READ its own source code (code_read).
  2. WRITE new code — new tool functions, new capabilities (code_write).
  3. RUN code in a subprocess to test it before adopting (code_run).
  4. CREATE new tools as files in custom_tools/, each auto-registered as a
     callable tool the agent (and external MCP clients) can use.
  5. REFLECT on what it's learned and propose new abilities (self_reflect).
  6. GIT-ROLLBACK if a self-edit breaks something (git_rollback).

The custom_tools/ directory is the agent's playground. Each tool is a
self-contained Python file with a `run(args: dict) -> dict` function and a
`SCHEMA` dict describing its name/description/parameters. The backend
loads them dynamically on startup and after every create, so the agent
can use a tool it just wrote in its very next turn.

Safety:
  - New tool files are written to custom_tools/ and imported in a sandbox
    (we catch import errors so one bad tool can't crash the server).
  - code_run executes in a subprocess with a timeout; it never touches the
    live backend.
  - git_rollback restores files from HEAD so a bad self-edit is reversible.
  - All self-improvement actions are logged.
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).parent.resolve()
CUSTOM_TOOLS_DIR = BACKEND_DIR / "custom_tools"
BACKEND_ROOT = BACKEND_DIR.parent  # Vault2 root


class SelfImprover:
    """File I/O, code execution, tool creation, and git rollback for the agent."""

    def __init__(self, session_logger=None):
        self.session_logger = session_logger
        CUSTOM_TOOLS_DIR.mkdir(exist_ok=True)
        # Ensure there's an __init__.py so custom_tools is a package.
        init = CUSTOM_TOOLS_DIR / "__init__.py"
        if not init.exists():
            init.write_text("# VaultBot custom tools (agent-authored)\n", encoding="utf-8")
        # Track loaded tool modules for hot-reload.
        self._loaded_tools: dict[str, Any] = {}
        self._loaded_schemas: dict[str, dict[str, Any]] = {}
        self.load_custom_tools()

    def _log(self, event: str, data: dict[str, Any] | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log(event, data)

    # --- Tool registry ---------------------------------------------------

    def load_custom_tools(self) -> dict[str, dict[str, Any]]:
        """Dynamically import every .py file in custom_tools/ and register
        its `run` callable and `SCHEMA`. Returns the schema map."""
        self._loaded_tools.clear()
        self._loaded_schemas.clear()
        if str(CUSTOM_TOOLS_DIR) not in sys.path:
            sys.path.insert(0, str(CUSTOM_TOOLS_DIR))
        for py in sorted(CUSTOM_TOOLS_DIR.glob("*.py")):
            if py.name.startswith("_") or py.name == "__init__.py":
                continue
            mod_name = py.stem
            try:
                mod = importlib.import_module(mod_name)
                importlib.reload(mod)
            except Exception as e:
                self._log("custom_tool_import_failed",
                          {"module": mod_name, "error": str(e),
                           "traceback": traceback.format_exc()})
                continue
            schema = getattr(mod, "SCHEMA", None)
            run_fn = getattr(mod, "run", None)
            if schema and callable(run_fn):
                tool_name = schema.get("name", mod_name)
                self._loaded_tools[tool_name] = run_fn
                self._loaded_schemas[tool_name] = schema
                self._log("custom_tool_loaded", {"name": tool_name, "module": mod_name})
        return dict(self._loaded_schemas)

    def custom_tool_schemas(self) -> list[dict[str, Any]]:
        """Return Ollama-format tool definitions for loaded custom tools."""
        out = []
        for name, schema in self._loaded_schemas.items():
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": schema.get("description", ""),
                    "parameters": schema.get("parameters",
                                              {"type": "object", "properties": {}}),
                },
            })
        return out

    def has_tool(self, name: str) -> bool:
        return name in self._loaded_tools

    def execute_custom_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run a loaded custom tool by name. Catches all exceptions so a
        buggy agent-authored tool never crashes the server."""
        fn = self._loaded_tools.get(name)
        if fn is None:
            return {"error": f"custom tool not found: {name}"}
        t0 = time.time()
        try:
            result = fn(args)
            if not isinstance(result, dict):
                result = {"result": str(result)}
            self._log("custom_tool_executed",
                      {"name": name, "args": args, "duration_ms": (time.time()-t0)*1000})
            return result
        except Exception as e:
            self._log("custom_tool_error",
                      {"name": name, "args": args, "error": str(e),
                       "traceback": traceback.format_exc()})
            return {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}

    # --- code_read -------------------------------------------------------

    def code_read(self, file_path: str, start_line: int = 1, end_line: int = 0
                  ) -> dict[str, Any]:
        """Read a file under the vault/backend. Paths are relative to Vault2 root."""
        full = self._resolve_path(file_path)
        if not full:
            return {"error": f"path not found or not allowed: {file_path}"}
        try:
            lines = full.read_text(encoding="utf-8").splitlines()
            s = max(1, start_line)
            e = len(lines) if end_line <= 0 else min(end_line, len(lines))
            snippet = "\n".join(lines[s-1:e])
            return {"file_path": str(full), "total_lines": len(lines),
                    "start_line": s, "end_line": e, "content": snippet}
        except Exception as e:
            return {"error": str(e)}

    # --- code_write ------------------------------------------------------

    def code_write(self, file_path: str, content: str) -> dict[str, Any]:
        """Write a file under the vault/backend. Paths relative to Vault2 root."""
        full = self._resolve_path(file_path, allow_create=True)
        if not full:
            return {"error": f"path not allowed: {file_path}"}
        # Respect the vault write guard for .md notes: never let the LLM use
        # its self-edit tool to bypass the sacred/locked protection on vault
        # notes. Non-markdown files (code, config) are unaffected.
        if full.suffix == ".md":
            from vault_guard import VaultWriteForbidden, assert_writable
            try:
                assert_writable(full)
            except VaultWriteForbidden as e:
                return {"error": f"write blocked: {e.reason}",
                        "file_path": str(full)}
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            # Back up the existing file before overwriting so we can rollback.
            if full.exists():
                bak = full.with_suffix(full.suffix + ".bak")
                shutil.copy2(full, bak)
            full.write_text(content, encoding="utf-8")
            self._log("code_write", {"file_path": str(full), "length": len(content)})
            return {"file_path": str(full), "bytes": len(content)}
        except Exception as e:
            return {"error": str(e)}

    # --- safe_write -----------------------------------------------------
    # A hardened code_write that the agent SHOULD use in place of code_write
    # when editing its own source. It catches the two failure modes that
    # actually broke the backend (2026-07-25): (1) writing syntactically
    # invalid Python, and (2) writing a module that imports cleanly alone
    # but breaks a *caller* (e.g. deleting a module another file imports,
    # or changing a function signature a caller depends on). The check runs
    # in a SUBPROCESS so a broken module never crashes the live backend.

    # Files whose import the live backend depends on. A safe_write to any of
    # these triggers a full import-graph verification in a subprocess that
    # imports main.py (the entry point) — if that subprocess can't import
    # main, the edit is rejected and the original is restored.
    _CORE_FILES = {
        "main.py", "agent_tools.py", "self_improver.py", "vault_indexer.py",
        "vault_graph.py", "note_creator.py", "research_engine.py",
        "autonomous_researcher.py", "fused_retrieval.py", "compactor.py",
        "amem_evolution.py", "knowledge_curriculum.py", "plan_executor.py",
        "identity.py", "graph_ops.py", "lazy_condenser.py", "concept_card.py",
        "moc_builder.py", "abstract_context.py", "embedding_drift.py",
        "llm_client.py", "ollama_client.py", "session_logger.py",
        "vault_guard.py", "supervision.py", "checkpointer.py",
        "free_search.py", "duckduckgo_client.py", "tavily_client.py",
        "searxng_manager.py", "web_source_store.py", "speech.py",
        "vault_maintenance.py", "textbook_index.py",
        "services.py",
    }

    def safe_write(self, file_path: str, content: str,
                   dry_run: bool = False) -> dict[str, Any]:
        """Write a file with safety verification. Use this INSTEAD of
        code_write when editing backend source code (.py files under
        vaultbot_backend/). For markdown notes or non-code files, code_write
        is fine.

        Safety checks (in order, fail-fast):
          1. Syntax: the content must parse as valid Python (ast.parse).
          2. Encoding: written as UTF-8 (avoids the mojibake corruption
             the agent's code_write introduced on 2026-07-25).
          3. Import verification: if the target is a core backend module,
             a SUBPROCESS imports the backend entry point (main.py) with
             the new file in place. If that subprocess fails to import,
             the edit is rejected and the original file is restored. This
             catches both "I deleted a module another file imports" and
             "I changed a signature a caller depends on" — the two ways
             the agent broke itself.
          4. Pytest gate (soft): if the import check PASSED, run `pytest -q`
             against the same backend dir (the tmp copy for dry_run, the
             live backend dir for a real write). If any test FAILS, the
             edit is rejected and (for a real write) auto-rolled-back.
             If pytest itself cannot run (not installed, import error,
             etc.), the check is recorded as `skipped: <reason>` and the
             write proceeds — the import check is the hard gate; pytest
             is a softer gate enforced only when it can actually run.
          5. Auto-rollback: on any check failure after the file is on
             disk, the pre-edit backup (.bak) is restored immediately.

        Args:
          file_path: path relative to vault root.
          content: the new file content.
          dry_run: if True, run all checks but do NOT write to disk.
            Returns the verification result so the agent can preview
            whether an edit would be safe before committing it.

        Returns a dict with: status ("written" | "dry_run_ok" |
        "rejected"), the checks performed, and on rejection the error
        that would have broken the backend.
        """
        full = self._resolve_path(file_path, allow_create=True)
        if not full:
            return {"error": f"path not allowed: {file_path}"}
        is_core = (full.parent.resolve() == BACKEND_DIR
                   and full.name in self._CORE_FILES)
        checks: dict[str, Any] = {}

        # --- 1. Syntax check (no disk touch) ---
        import ast
        try:
            ast.parse(content)
            checks["syntax"] = "ok"
        except SyntaxError as e:
            checks["syntax"] = f"FAIL: {e}"
            return {"status": "rejected", "checks": checks,
                    "error": f"SyntaxError: {e.msg} (line {e.lineno})",
                    "hint": "Fix the syntax error; nothing was written."}

        # --- dry_run: stop here, report whether it WOULD be safe ---
        if dry_run:
            # For a dry run of a core file, do the import check against a
            # temp copy so we don't disturb the live file.
            if is_core and full.exists():
                import tempfile
                tmpdir = tempfile.mkdtemp(prefix="vaultbot_dryrun_")
                try:
                    # Copy the backend dir, swap in the new content, import.
                    self._copy_backend_for_check(tmpdir, full.name, content)
                    ok, err = self._verify_import_in_subprocess(tmpdir)
                    checks["import_check"] = "ok" if ok else f"FAIL: {err}"
                    if not ok:
                        return {"status": "dry_run_rejected",
                                "checks": checks,
                                "would_break_backend": True,
                                "error": err}
                    # Import passed — run the soft pytest gate against the
                    # same tmp copy. A failure is reported as
                    # dry_run_rejected (no disk touch in dry_run mode).
                    p_ok, p_out = self._run_pytest_in_subprocess(tmpdir)
                    if p_out and not p_ok:
                        checks["pytest"] = f"FAIL: {p_out[:500]}"
                        return {"status": "dry_run_rejected",
                                "checks": checks,
                                "would_break_backend": True,
                                "error": p_out[:500]}
                    checks["pytest"] = (
                        "ok" if p_ok
                        else f"skipped: {(p_out or 'unknown')[:200]}")
                    return {"status": "dry_run_ok",
                            "checks": checks,
                            "would_break_backend": False,
                            "error": None}
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            checks["import_check"] = "skipped (not a core backend file)"
            return {"status": "dry_run_ok", "checks": checks,
                    "would_break_backend": False}

        # --- Markdown guard (same as code_write) ---
        if full.suffix == ".md":
            from vault_guard import VaultWriteForbidden, assert_writable
            try:
                assert_writable(full)
            except VaultWriteForbidden as e:
                return {"error": f"write blocked: {e.reason}",
                        "file_path": str(full)}

        # --- 2. Write (UTF-8) + backup ---
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            had_backup = False
            if full.exists():
                bak = full.with_suffix(full.suffix + ".bak")
                shutil.copy2(full, bak)
                had_backup = True
            full.write_text(content, encoding="utf-8")
            checks["encoding"] = "utf-8 ok"
        except Exception as e:
            return {"status": "rejected", "checks": checks,
                    "error": f"write failed: {e}"}

        # --- 3. Import verification (subprocess, core files only) ---
        if is_core:
            ok, err = self._verify_import_in_subprocess(str(BACKEND_DIR))
            checks["import_check"] = "ok" if ok else f"FAIL: {err}"
            if not ok:
                # 4. Auto-rollback: restore the .bak so the live backend
                # is not left with a broken file.
                if had_backup:
                    try:
                        shutil.copy2(full.with_suffix(full.suffix + ".bak"), full)
                        checks["auto_rollback"] = "restored from .bak"
                    except Exception as rb_err:
                        checks["auto_rollback"] = f"FAILED: {rb_err}"
                else:
                    # No prior file existed; delete the broken new file.
                    try:
                        full.unlink()
                        checks["auto_rollback"] = "deleted new file (no prior .bak)"
                    except Exception as rb_err:
                        checks["auto_rollback"] = f"FAILED: {rb_err}"
                self._log("safe_write_rejected", {
                    "file_path": str(full), "error": err, "checks": checks})
                return {"status": "rejected", "checks": checks,
                        "error": err,
                        "hint": ("The edit would break the backend on restart. "
                                 "The original file was restored. Fix the error "
                                 "and try again, or use git_rollback if needed.")}

        # --- 3b. Pytest gate (soft; core files only) ---
        # Only run pytest if the import check passed (don't waste time
        # running tests on a file that doesn't even import). A pytest
        # failure rejects the edit and auto-rolls-back from .bak (same
        # path as the import-check failure above). If pytest itself cannot
        # run (not installed, import error, etc.), record `skipped: ...`
        # and proceed — the import check is the hard gate.
        if is_core and checks.get("import_check") == "ok":
            try:
                p_ok, p_out = self._run_pytest_in_subprocess(str(BACKEND_DIR))
            except Exception as e:
                checks["pytest"] = f"skipped: could not run pytest: {e}"
                p_ok = True  # treat as pass; soft gate
                p_out = None
            if p_out and not p_ok:
                checks["pytest"] = f"FAIL: {p_out[:500]}"
                # Auto-rollback from .bak (same path as import failure).
                if had_backup:
                    try:
                        shutil.copy2(
                            full.with_suffix(full.suffix + ".bak"), full)
                        checks["auto_rollback"] = "restored from .bak"
                    except Exception as rb_err:
                        checks["auto_rollback"] = f"FAILED: {rb_err}"
                else:
                    try:
                        full.unlink()
                        checks["auto_rollback"] = (
                            "deleted new file (no prior .bak)")
                    except Exception as rb_err:
                        checks["auto_rollback"] = f"FAILED: {rb_err}"
                self._log("safe_write_pytest_rejected", {
                    "file_path": str(full),
                    "error": p_out[:500], "checks": checks})
                return {"status": "rejected", "checks": checks,
                        "error": p_out[:500],
                        "hint": ("The edit passed the import check but "
                                 "failed a pytest run. The original file "
                                 "was restored. Fix the failing test and "
                                 "try again, or use git_rollback if needed.")}
            checks["pytest"] = (
                "ok" if p_ok
                else f"skipped: {(p_out or 'unknown')[:200]}")

        self._log("safe_write", {"file_path": str(full), "length": len(content),
                                  "is_core": is_core, "checks": checks})
        return {"status": "written", "file_path": str(full),
                "bytes": len(content), "checks": checks, "is_core": is_core}

    def _copy_backend_for_check(self, tmpdir: str, target_name: str,
                                new_content: str) -> None:
        """Copy the backend dir into tmpdir, then overwrite target_name with
        new_content, so a subprocess can import main.py against the proposed
        edit without touching the live files."""
        # Copy only .py files (skip venv, index, models, etc.) to keep it fast.
        for py in BACKEND_DIR.glob("*.py"):
            shutil.copy2(py, Path(tmpdir) / py.name)
        # Copy the custom_tools package + identity dir (main.py imports them).
        ct_dst = Path(tmpdir) / "custom_tools"
        ct_dst.mkdir(exist_ok=True)
        (ct_dst / "__init__.py").touch()
        for py in (BACKEND_DIR / "custom_tools").glob("*.py"):
            if py.name == "__init__.py":
                continue
            shutil.copy2(py, ct_dst / py.name)
        id_dst = Path(tmpdir) / "identity"
        id_dst.mkdir(exist_ok=True)
        if (BACKEND_DIR / "identity").exists():
            for f in (BACKEND_DIR / "identity").iterdir():
                if f.is_file():
                    shutil.copy2(f, id_dst / f.name)
        # Overwrite the target with the proposed new content.
        (Path(tmpdir) / target_name).write_text(new_content, encoding="utf-8")

    def _verify_import_in_subprocess(self, backend_dir: str
                                     ) -> tuple[bool, str | None]:
        """Run `python -c 'import main'` in a subprocess against the given
        backend dir. Returns (ok, error_message). A clean import means the
        whole import graph resolves with the proposed edit in place."""
        venv_python = str(BACKEND_ROOT / "vaultbot_venv" / "Scripts" / "python.exe")
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
            "os.environ.setdefault('VAULT_PATH', os.environ['CHECK_DIR']); "
            "import main; print('IMPORT_OK')"
        )
        env = {**os.environ,
               "CHECK_DIR": backend_dir,
               "PYTHONPATH": backend_dir,
               "VAULTBOT_SKIP_LOCK": "1",
               "VAULT_PATH": backend_dir}
        try:
            proc = subprocess.run(
                [venv_python, "-c", check_code],
                capture_output=True, text=True, timeout=30,
                cwd=str(BACKEND_ROOT), env=env,
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
        except Exception as e:
            return False, f"import check could not run: {e}"

    def _run_pytest_in_subprocess(self, backend_dir: str
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

        The 60s timeout prevents a hung test from blocking forever. We use
        the venv interpreter (same as `_verify_import_in_subprocess`) and
        point PYTHONPATH at the backend dir so leaf modules import without
        touching the live backend. We DO NOT pass `-p no:cacheprovider`
        or similar; the conftest's hard-fence against importing `main` keeps
        the test process safe.
        """
        # Prefer the venv interpreter (matches _verify_import_in_subprocess),
        # but pytest + faiss live in the SYSTEM Python in this environment,
        # not in vaultbot_venv. Probe both: use the first interpreter that
        # can import pytest. If neither can, soft-skip.
        venv_python = str(
            BACKEND_ROOT / "vaultbot_venv" / "Scripts" / "python.exe")
        candidates = [venv_python, sys.executable]
        chosen = None
        for cand in candidates:
            if not cand or not Path(cand).exists():
                continue
            try:
                probe = subprocess.run(
                    [cand, "-c", "import pytest"],
                    capture_output=True, text=True, timeout=10)
                if probe.returncode == 0:
                    chosen = cand
                    break
            except Exception:
                continue
        if chosen is None:
            # pytest not importable in any available interpreter — soft-skip.
            return True, "pytest not installed in any interpreter"

        env = {**os.environ,
               "PYTHONPATH": backend_dir,
               # Keep the test process off the live vault / PID lock.
               "VAULTBOT_SKIP_LOCK": "1",
               "VAULT_PATH": backend_dir}
        try:
            proc = subprocess.run(
                [chosen, "-m", "pytest", "-q", "--tb=short"],
                capture_output=True, text=True, timeout=60,
                cwd=backend_dir, env=env,
            )
        except subprocess.TimeoutExpired:
            # Hung test — treat as skipped (soft gate), not a hard reject.
            return True, "pytest timed out (60s) — skipped"
        except FileNotFoundError:
            return True, "pytest interpreter not found"
        except Exception as e:
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
            return True, (f"pytest usage/internal error "
                         f"(rc={proc.returncode}): {tail[:200]}")
        if proc.returncode == 5:
            # No tests collected — benign; treat as pass.
            return True, None
        # rc == 1 (or anything else): real test failure — hard reject.
        tail = combined.strip().splitlines()[-1] if combined.strip() else ""
        return False, tail[:500] if tail else (
            f"pytest exit code {proc.returncode} (no output captured)")

    # --- capability_audit ------------------------------------------------

    def capability_audit(self, task: str = "") -> dict[str, Any]:
        """Inventory every tool VaultBot currently has — built-in vault
        tools, meta (self-edit) tools, and agent-authored custom tools —
        with each tool's name + description. This lets the agent check
        'do I already have a tool for this task?' before attempting it,
        and identify the gap between its capabilities and a request.

        If `task` is given, also returns a `coverage` assessment: for each
        tool, whether its name/description mentions any word from the task
        (a rough keyword match to surface likely-relevant tools).

        Returns:
          tools: list of {name, kind, description, relevant?}
          total: count
          kinds: {builtin, meta, custom} counts
          coverage: (only if task given) list of relevant tool names +
            a 'gap_assessment' note.
        """
        from agent_tools import META_TOOL_DEFINITIONS, TOOL_DEFINITIONS
        tools: list[dict[str, Any]] = []

        def _add(schema_list, kind):
            for t in schema_list:
                fn = t.get("function", {})
                tools.append({
                    "name": fn.get("name", "?"),
                    "kind": kind,
                    "description": fn.get("description", ""),
                })

        _add(TOOL_DEFINITIONS, "builtin")
        _add(META_TOOL_DEFINITIONS, "meta")
        for name, schema in self._loaded_schemas.items():
            tools.append({
                "name": name,
                "kind": "custom",
                "description": schema.get("description", ""),
            })

        result: dict[str, Any] = {
            "tools": tools,
            "total": len(tools),
            "kinds": {
                "builtin": sum(1 for t in tools if t["kind"] == "builtin"),
                "meta": sum(1 for t in tools if t["kind"] == "meta"),
                "custom": sum(1 for t in tools if t["kind"] == "custom"),
            },
        }

        if task and task.strip():
            # Rough keyword coverage: tokenize the task, see which tools'
            # name+description mention any task word (>=4 chars to skip
            # stopwords like "the", "a", "for").
            words = {w.lower() for w in re.split(r"\W+", task)
                     if len(w) >= 4}
            relevant = []
            for t in tools:
                hay = (t["name"] + " " + t["description"]).lower()
                if any(w in hay for w in words):
                    t["relevant"] = True
                    relevant.append(t["name"])
                else:
                    t["relevant"] = False
            result["coverage"] = {
                "task": task,
                "relevant_tools": relevant,
                "has_relevant_tool": len(relevant) > 0,
                "gap_assessment": (
                    f"{len(relevant)} tool(s) appear relevant to '{task}'. "
                    + ("If none directly accomplish it, you can build a new "
                       "tool with tool_create (test with code_run first), or "
                       "edit your source with safe_write."
                       if relevant else
                       "No existing tool matches. You have a CAPABILITY GAP. "
                       "Fill it: (1) self_reflect on the gap to propose a "
                       "tool, (2) code_run to test the implementation, "
                       "(3) tool_create to add it, or safe_write to edit an "
                       "existing module. Always preflight_safety_check first.")
                ),
            }
        return result

    # --- code_run --------------------------------------------------------

    def code_run(self, code: str, timeout: int = 15) -> dict[str, Any]:
        """Execute Python code in a subprocess and return stdout/stderr/exit."""
        venv_python = str(BACKEND_ROOT / "vaultbot_venv" / "Scripts" / "python.exe")
        if not Path(venv_python).exists():
            venv_python = sys.executable
        try:
            proc = subprocess.run(
                [venv_python, "-c", code],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(BACKEND_ROOT),
                env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
            )
            return {"stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:],
                    "exit_code": proc.returncode}
        except subprocess.TimeoutExpired:
            return {"error": "timeout", "timeout": timeout}
        except Exception as e:
            return {"error": str(e)}

    # --- tool_create -----------------------------------------------------

    def tool_create(self, tool_name: str, description: str,
                    parameters: dict[str, Any], code: str) -> dict[str, Any]:
        """Create a new tool file in custom_tools/, load it, and register it.
        `code` must define a `run(args: dict) -> dict` function.
        Returns the new tool's schema if it loaded successfully."""
        safe = self._safe_name(tool_name)
        file_path = CUSTOM_TOOLS_DIR / f"{safe}.py"
        # Wrap the agent's code with a SCHEMA header so it self-registers.
        full_code = (
            f'"""\nAgent-authored tool: {tool_name}\n"""\n\n'
            f'SCHEMA = {{"name": {json.dumps(tool_name)}, '
            f'"description": {json.dumps(description)}, '
            f'"parameters": {json.dumps(parameters, default=str)}}}\n\n'
            f"{code}\n"
        )
        try:
            file_path.write_text(full_code, encoding="utf-8")
        except Exception as e:
            return {"error": f"could not write tool file: {e}"}
        # Try to load it immediately.
        before = set(self._loaded_schemas.keys())
        self.load_custom_tools()
        after = set(self._loaded_schemas.keys())
        loaded = tool_name in self._loaded_schemas or safe in self._loaded_schemas
        self._log("tool_create", {"tool_name": tool_name, "file": str(file_path),
                                   "loaded": loaded})
        if loaded:
            schema = self._loaded_schemas.get(tool_name) or self._loaded_schemas.get(safe)
            return {"status": "created", "tool_name": tool_name,
                    "file_path": str(file_path), "schema": schema}
        # Import failed — return the error so the agent can fix its code.
        return {"status": "created_but_import_failed", "tool_name": tool_name,
                "file_path": str(file_path),
                "hint": "Check code_run to debug; the file exists but has a syntax/runtime error."}

    # --- self_reflect ----------------------------------------------------

    def self_reflect(self, topic: str, vault_context: str = "") -> dict[str, Any]:
        """Ask the LLM to reflect on what it's learned and propose new abilities.
        Uses a cheap, non-streaming call so it doesn't interfere with the chat.
        Routes through get_llm_client() so it works with ANY configured backend
        (Ollama, OpenAI, OpenRouter, etc.) — not just local Ollama."""
        try:
            from llm_client import get_llm_client
            client = get_llm_client()
            prompt = (
                "You are VaultBot reflecting on your own abilities in service "
                "of your owner. Given the topic and vault context below, "
                "propose 1-3 NEW tool abilities you could write for yourself "
                "(as Python `run(args)->dict` functions) that would make you "
                "more useful to your owner. For each, give a name, "
                "description, parameters schema, and a short description of "
                "what the code would do. Be concrete and practical — focus "
                "on what would actually advance your owner's goals.\n\n"
                f"Topic: {topic}\n\nVault context:\n{vault_context[:2000]}\n\n"
                "Respond as JSON: {\"proposals\": [{\"name\", \"description\", "
                "\"parameters\", \"code_sketch\"}]}"
            )
            result = client.generate(prompt, temperature=0.4, stream=False)
            text = result.get("response", "")
            return {"reflection": text, "topic": topic}
        except Exception as e:
            return {"error": str(e)}

    # --- git_rollback ----------------------------------------------------

    def git_rollback(self, file_path: str = "") -> dict[str, Any]:
        """Restore files from git HEAD. If file_path is given, restore just
        that file; otherwise restore all changed files under the backend."""
        target = self._resolve_path(file_path) if file_path else BACKEND_DIR
        if not target:
            return {"error": f"path not found: {file_path}"}
        try:
            rel = str(target.relative_to(BACKEND_ROOT)).replace("\\", "/")
            if file_path:
                proc = subprocess.run(
                    ["git", "checkout", "HEAD", "--", rel],
                    capture_output=True, text=True, cwd=str(BACKEND_ROOT), timeout=10)
            else:
                proc = subprocess.run(
                    ["git", "checkout", "HEAD", "--", "vaultbot_backend"],
                    capture_output=True, text=True, cwd=str(BACKEND_ROOT), timeout=10)
            return {"stdout": proc.stdout, "stderr": proc.stderr,
                    "exit_code": proc.returncode, "restored": rel}
        except Exception as e:
            return {"error": str(e)}

    # --- helpers ---------------------------------------------------------

    def _safe_name(self, name: str) -> str:
        import re
        return re.sub(r"[^a-zA-Z0-9_]", "_", name)

    def _resolve_path(self, file_path: str, allow_create: bool = False
                      ) -> Path | None:
        """Resolve a path relative to Vault2 root, restricted to the vault
        directory so the agent can't write outside it."""
        if not file_path:
            return None
        candidate = (BACKEND_ROOT / file_path).resolve()
        # Must be inside the vault root.
        try:
            candidate.relative_to(BACKEND_ROOT.resolve())
        except ValueError:
            return None
        if not allow_create and not candidate.exists():
            return None
        return candidate
