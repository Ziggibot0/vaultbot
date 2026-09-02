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

import ast
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, ClassVar

import code_verify
import safe_writer
from config import TUNABLES
from paths import FRAMEWORK_ROOT
from subprocess_utils import preexec_fn, scrubbed_env
from subprocess_utils import run as _subprocess_run

BACKEND_DIR = Path(__file__).parent.resolve()
CUSTOM_TOOLS_DIR = BACKEND_DIR / "custom_tools"
BACKEND_ROOT = FRAMEWORK_ROOT
TRASH_DIR = (
    BACKEND_DIR / "trash" / "backups"
)  # all .bak files go here, not alongside source

# Custom tools that are gated behind VAULTBOT_ALLOW_CONTRIBUTIONS=true.
# When contributions are off, these tools are not loaded at all — their
# schemas never reach the LLM (zero context bloat) and they can't be
# called. Each tool also checks the env var at call time as
# defence-in-depth. See [[Community-Contribution-System]].
_CONTRIBUTIONS_GATED_TOOLS: frozenset[str] = frozenset(
    {
        "github_issues",  # read/comment/close/label/create GitHub issues
        "submit_contribution",  # submit PRs (fork-based or direct)
        "review_contributions",  # review open PRs (maintainer side)
        "torture_test",  # torture-test a PR before merge (maintainer side)
        "pr_feedback",  # check PR CI/reviews (contributor feedback loop)
    }
)


def _contributions_enabled() -> bool:
    """Return whether community contributions are opted in (live value)."""
    from live_config import allow_contributions

    return allow_contributions()


class SelfImprover:
    """File I/O, code execution, tool creation, and git rollback for the agent."""

    def __init__(self, session_logger=None):
        self.session_logger = session_logger
        CUSTOM_TOOLS_DIR.mkdir(exist_ok=True)
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        # Ensure there's an __init__.py so custom_tools is a package.
        init = CUSTOM_TOOLS_DIR / "__init__.py"
        if not init.exists():
            init.write_text(
                "# VaultBot custom tools (agent-authored)\n", encoding="utf-8"
            )
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
            # Contributions-gated tools: skip loading entirely when the user
            # hasn't opted into community contributions. This keeps the schema
            # out of the LLM context (no bloat from a tool that will never be
            # used) and prevents the tool from being callable at all. Each
            # gated tool also has a call-time check as defence-in-depth.
            if mod_name in _CONTRIBUTIONS_GATED_TOOLS and not _contributions_enabled():
                self._log(
                    "custom_tool_skipped_contributions_off",
                    {"name": mod_name, "module": mod_name},
                )
                continue
            # Import using the full package-qualified path (custom_tools.<stem>)
            # so that `from custom_tools.ask_user import _pending_requests` in
            # other modules (e.g. routers/system.py /user_response endpoint)
            # resolves to the SAME module object in sys.modules.  Importing by
            # bare stem ("ask_user") creates a separate sys.modules entry that
            # diverges from the package-qualified import — a classic Python
            # module-identity split that causes shared state (like
            # _pending_requests) to be invisible across the two import paths.
            full_name = f"custom_tools.{mod_name}"
            try:
                mod = importlib.import_module(full_name)
                importlib.reload(mod)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log(
                    "custom_tool_import_failed",
                    {
                        "module": mod_name,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
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
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": schema.get("description", ""),
                        "parameters": schema.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
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
            self._log(
                "custom_tool_executed",
                {"name": name, "args": args, "duration_ms": (time.time() - t0) * 1000},
            )
            return result
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log(
                "custom_tool_error",
                {
                    "name": name,
                    "args": args,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                },
            )
            return {
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
            }

    # --- code_read -------------------------------------------------------

    def code_read(
        self, file_path: str, start_line: int = 1, end_line: int = 0
    ) -> dict[str, Any]:
        """Read a file under the vault/backend. Paths are relative to the vault root."""
        full = self._resolve_path(file_path)
        if not full:
            return {"error": f"path not found or not allowed: {file_path}"}
        try:
            lines = full.read_text(encoding="utf-8").splitlines()
            s = max(1, start_line)
            e = len(lines) if end_line <= 0 else min(end_line, len(lines))
            snippet = "\n".join(lines[s - 1 : e])
            return {
                "file_path": str(full),
                "total_lines": len(lines),
                "start_line": s,
                "end_line": e,
                "content": snippet,
            }
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return {"error": str(e)}

    # --- code_write ------------------------------------------------------

    def code_write(self, file_path: str, content: str) -> dict[str, Any]:
        """Write a file under the vault/backend. Paths relative to the vault root."""
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
                return {"error": f"write blocked: {e.reason}", "file_path": str(full)}
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            # Back up the existing file before overwriting so we can rollback.
            had_backup = False
            if full.exists():
                bak = self._backup_path(full)
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(full, bak)
                had_backup = True
            full.write_text(content, encoding="utf-8")
            # Clean up backup on success — the write is verified.
            if had_backup:
                with contextlib.suppress(Exception):
                    bak.unlink()
            self._log("code_write", {"file_path": str(full), "length": len(content)})
            return {"file_path": str(full), "bytes": len(content)}
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
    _CORE_FILES: ClassVar[set[str]] = {
        "main.py",
        "agent_tools.py",
        "self_improver.py",
        "vault_indexer.py",
        "vault_graph.py",
        "note_creator.py",
        "research_engine.py",
        "research_source_acquirer.py",
        "fused_retrieval.py",
        "amem_evolution.py",
        "knowledge_curriculum.py",
        "plan_executor.py",
        "identity.py",
        "graph_ops.py",
        "lazy_condenser.py",
        "concept_card.py",
        "moc_builder.py",
        "abstract_context.py",
        "embedding_drift.py",
        "llm_client.py",
        "ollama_client.py",
        "session_logger.py",
        "vault_guard.py",
        "supervision.py",
        "free_search.py",
        "duckduckgo_client.py",
        "tavily_client.py",
        "searxng_manager.py",
        "web_source_store.py",
        "vault_maintenance.py",
        "textbook_index.py",
        "services.py",
    }

    def safe_write(
        self,
        file_path: str,
        content: str,
        dry_run: bool = False,
        doc_source: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Write a Python file with safety verification (delegates to safe_writer).

        ``doc_source`` is the official-docs URL (or list of URLs) the edit
        was checked against. Required when the content imports any
        non-VaultBot module (stdlib or third-party) — see the doc-source
        gate in safe_writer.safe_write.
        """
        return safe_writer.safe_write(
            file_path,
            content,
            dry_run,
            BACKEND_DIR,
            BACKEND_ROOT,
            TRASH_DIR,
            self._CORE_FILES,
            self._log,
            self._verify_import_targets,
            self._copy_backend_for_check,
            self._verify_import_in_subprocess,
            self._run_pytest_in_subprocess,
            doc_source,
        )

    def js_safe_write(
        self, file_path: str, content: str, dry_run: bool = False
    ) -> dict[str, Any]:
        """Write a JavaScript file with syntax validation (delegates to safe_writer)."""
        return safe_writer.js_safe_write(
            file_path,
            content,
            dry_run,
            BACKEND_ROOT,
            TRASH_DIR,
            self._log,
            self._verify_js_load,
        )

    def _copy_backend_for_check(
        self, tmpdir: str, target_name: str, new_content: str
    ) -> None:
        """Copy the backend dir into tmpdir for subprocess checks (delegates
        to code_verify)."""
        code_verify.copy_backend_for_check(
            tmpdir, target_name, new_content, BACKEND_DIR
        )

    def _verify_import_in_subprocess(self, backend_dir: str) -> tuple[bool, str | None]:
        """Import-check the backend in a subprocess (delegates to code_verify)."""
        return code_verify.verify_import_in_subprocess(backend_dir, BACKEND_ROOT)

    def _verify_import_targets(
        self, content: str, backend_dir: str
    ) -> tuple[bool, str | None]:
        """Statically verify import targets resolve (delegates to code_verify)."""
        return code_verify.verify_import_targets(content, backend_dir, BACKEND_ROOT)

    def _verify_js_load(
        self, content: str, timeout_s: int = 8
    ) -> tuple[bool, str | None]:
        """Require() a JS module in a child Node process (delegates to code_verify)."""
        return code_verify.verify_js_load(content, timeout_s)

    def _verify_startup_smoke(
        self, backend_dir: str, timeout_s: int = 40
    ) -> tuple[bool, str | None]:
        """Start the backend in a subprocess and hit /health (delegates to
        code_verify)."""
        return code_verify.verify_startup_smoke(backend_dir, timeout_s, BACKEND_ROOT)

    def _run_pytest_in_subprocess(
        self, backend_dir: str, target_file: str | None = None
    ) -> tuple[bool, str | None]:
        """Run pytest in a subprocess (delegates to code_verify)."""
        return code_verify.run_pytest_in_subprocess(
            backend_dir, target_file, BACKEND_ROOT
        )

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
                tools.append(
                    {
                        "name": fn.get("name", "?"),
                        "kind": kind,
                        "description": fn.get("description", ""),
                    }
                )

        _add(TOOL_DEFINITIONS, "builtin")
        _add(META_TOOL_DEFINITIONS, "meta")
        for name, schema in self._loaded_schemas.items():
            tools.append(
                {
                    "name": name,
                    "kind": "custom",
                    "description": schema.get("description", ""),
                }
            )

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
            words = {w.lower() for w in re.split(r"\W+", task) if len(w) >= 4}
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
                    + (
                        "If none directly accomplish it, you can build a new "
                        "tool with tool_create (test with code_run first), or "
                        "edit your source with safe_write."
                        if relevant
                        else "No existing tool matches. You have a CAPABILITY GAP. "
                        "Fill it: (1) self_reflect on the gap to propose a "
                        "tool, (2) code_run to test the implementation, "
                        "(3) tool_create to add it, or safe_write to edit an "
                        "existing module. Always preflight_safety_check first."
                    )
                ),
            }
        return result

    # --- code_run --------------------------------------------------------

    def code_run(
        self, code: str, timeout: int = 15, allow_write: bool = False
    ) -> dict[str, Any]:
        """Execute Python code in a subprocess and return stdout/stderr/exit.

        CRASH FIX (agent_silent): previously used capture_output=True, which
        buffers the child's ENTIRE stdout/stderr in backend RAM. A verbose run
        (e.g. printing a large file) grew backend memory unboundedly and the
        single backend process was OOM-killed -> agent_silent. Now the child
        writes to temp files with a HARD BYTE CAP; the backend reads back only
        the tail. Backend memory stays flat no matter how much the child prints.

        READ-ONLY GUARD (issue #207): by default the child runs with a guard
        preamble that blocks file-write primitives (open 'w', Path.write_text,
        shutil.copy, os.remove, ...). code_run is for TESTING only — the only
        sanctioned way to modify backend source is the gated safe_write. Pass
        ``allow_write=True`` to skip the guard for the rare legitimate case
        (e.g. a test that must write a temp file).
        """
        venv_python = str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe")
        if not Path(venv_python).exists():
            venv_python = sys.executable

        # Prepend the read-only guard unless the caller explicitly opted out.
        # (issues #207 + #229: blocks writes, network egress, and reads of
        # secret files like .env / providers.json inside the repo root.)
        if not allow_write:
            from code_run_guard import build_guard_preamble

            code = build_guard_preamble(str(BACKEND_ROOT)) + "\n" + code

        out_path = err_path = None
        try:
            with (
                tempfile.NamedTemporaryFile(
                    mode="w+b", delete=False, prefix="cr_out_"
                ) as out_f,
                tempfile.NamedTemporaryFile(
                    mode="w+b", delete=False, prefix="cr_err_"
                ) as err_f,
            ):
                out_path, err_path = out_f.name, err_f.name
                proc = _subprocess_run(
                    [venv_python, "-c", code],
                    stdout=out_f,
                    stderr=err_f,
                    timeout=timeout,
                    cwd=str(BACKEND_ROOT),
                    # Scrubbed env: LLM-authored code must not see API keys,
                    # tokens, or passwords from the parent process. Only
                    # PYTHONPATH (non-secret) is added back.
                    env={**scrubbed_env(), "PYTHONPATH": str(BACKEND_DIR)},
                    # Resource limits (POSIX): mem/CPU/fork caps. None on
                    # Windows (subprocess rejects a non-None preexec_fn there),
                    # so the timeout is the only limit there.
                    preexec_fn=preexec_fn,
                )

            def _read_window(path, n, *, head: bool = False):
                with open(path, "rb") as f:
                    data = f.read()
                text = data.decode("utf-8", errors="replace")
                if head:
                    return text[:n]
                return text[-n:] if n > 0 else text

            def _read_stats(path):
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                with open(path, "rb") as f:
                    data = f.read()
                text = data.decode("utf-8", errors="replace")
                lines = len(text.splitlines()) if text else 0
                return size, lines, text

            stdout_size, stdout_lines, _ = _read_stats(out_path)
            stderr_size, stderr_lines, _ = _read_stats(err_path)
            stdout_head = _read_window(
                out_path, TUNABLES.code_run_stdout_tail, head=True
            )
            stdout_tail = _read_window(out_path, TUNABLES.code_run_stdout_tail)
            stderr_head = _read_window(
                err_path, TUNABLES.code_run_stderr_tail, head=True
            )
            stderr_tail = _read_window(err_path, TUNABLES.code_run_stderr_tail)
            stdout_truncated = stdout_size > TUNABLES.code_run_stdout_tail
            stderr_truncated = stderr_size > TUNABLES.code_run_stderr_tail
            truncated = stdout_truncated or stderr_truncated
            stdout_display = stdout_tail
            if stdout_truncated:
                stdout_display = (
                    "[... output truncated: showing only the tail of stdout; "
                    f"total_bytes={stdout_size}, total_lines={stdout_lines}, "
                    f"tail_chars={TUNABLES.code_run_stdout_tail} ...]\n"
                    f"{stdout_tail}"
                )
            stderr_display = stderr_tail
            if stderr_truncated:
                stderr_display = (
                    "[... output truncated: showing only the tail of stderr; "
                    f"total_bytes={stderr_size}, total_lines={stderr_lines}, "
                    f"tail_chars={TUNABLES.code_run_stderr_tail} ...]\n"
                    f"{stderr_tail}"
                )
            result = {
                "stdout": stdout_display,
                "stdout_head": stdout_head,
                "stdout_tail": stdout_tail,
                "stdout_total_bytes": stdout_size,
                "stdout_total_lines": stdout_lines,
                "stderr": stderr_display,
                "stderr_head": stderr_head,
                "stderr_tail": stderr_tail,
                "stderr_total_bytes": stderr_size,
                "stderr_total_lines": stderr_lines,
                "exit_code": proc.returncode,
                "truncated": truncated,
            }
            if truncated:
                result["output_truncated"] = True
                result["_truncation_notice"] = (
                    "Output was truncated to the head/tail window for this "
                    "tool result. "
                    f"stdout bytes={stdout_size}, stderr bytes={stderr_size}; "
                    f"stdout lines={stdout_lines}, stderr lines={stderr_lines}."
                )
            else:
                result["output_truncated"] = False
            return result
        except subprocess.TimeoutExpired:
            return {"error": "timeout", "timeout": timeout}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        finally:
            for tmp in (out_path, err_path):
                if tmp:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)

    # --- tool_create -----------------------------------------------------

    def tool_create(
        self,
        tool_name: str,
        description: str,
        parameters: dict[str, Any],
        code: str,
        doc_source: str | None = None,
    ) -> dict[str, Any]:
        """Create a new tool file in custom_tools/, load it, and register it.
        `code` must define a `run(args: dict) -> dict` function.
        Returns the new tool's schema if it loaded successfully.

        SECURITY GATE (issue #228): agent-authored tool code that imports
        exfiltration/escape primitives (network, raw OS/process, dynamic import)
        is rejected unless a `doc_source` is provided. The curated custom_tools/
        fleet is committed and trusted, so it never passes through this gate —
        only tools the agent authors at runtime are checked. See
        custom_tool_gate.py for the model and the residual-risk note."""
        import custom_tool_gate

        gated = custom_tool_gate.gate_agent_tool_code(code, BACKEND_DIR, doc_source)
        if gated["status"] == "rejected":
            self._log(
                "custom_tool_create_blocked",
                {
                    "tool_name": tool_name,
                    "dangerous_imports": gated["dangerous_imports"],
                },
            )
            return {
                "status": "rejected",
                "tool_name": tool_name,
                "dangerous_imports": gated["dangerous_imports"],
                "error": gated["error"],
                "hint": gated["hint"],
            }
        if doc_source and gated.get("dangerous_imports"):
            # A doc_source was provided to allow a dangerous import — log it so
            # the operator can review the agent's intent. This is NOT a silent
            # pass: it is a recorded, reviewable exception.
            self._log(
                "custom_tool_create_doc_sourced",
                {
                    "tool_name": tool_name,
                    "dangerous_imports": gated["dangerous_imports"],
                    "doc_source": doc_source,
                },
            )

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
        # Verify syntax BEFORE writing to disk — a broken tool file would
        # crash the hot-reload on the next tool_create.  This mirrors the
        # ast.parse guard in safe_write, applied to agent-authored tools.
        try:
            ast.parse(full_code)
        except SyntaxError as e:
            return {
                "error": f"syntax error in tool code: {e}",
                "tool_name": tool_name,
                "hint": "Fix the syntax error and try again. The file was NOT written.",
            }
        try:
            file_path.write_text(full_code, encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return {"error": f"could not write tool file: {e}"}
        # Try to load it immediately.
        set(self._loaded_schemas.keys())
        self.load_custom_tools()
        set(self._loaded_schemas.keys())
        loaded = tool_name in self._loaded_schemas or safe in self._loaded_schemas
        self._log(
            "tool_create",
            {"tool_name": tool_name, "file": str(file_path), "loaded": loaded},
        )
        if loaded:
            schema = self._loaded_schemas.get(tool_name) or self._loaded_schemas.get(
                safe
            )
            return {
                "status": "created",
                "tool_name": tool_name,
                "file_path": str(file_path),
                "schema": schema,
            }
        # Import failed — return the error so the agent can fix its code.
        return {
            "status": "created_but_import_failed",
            "tool_name": tool_name,
            "file_path": str(file_path),
            "hint": "Check code_run to debug; the file exists but has a "
            "syntax/runtime error.",
        }

    # --- self_reflect ----------------------------------------------------

    def self_reflect(self, topic: str, vault_context: str = "") -> dict[str, Any]:
        """Ask the LLM to reflect on what it's learned and propose new abilities.
        Uses a cheap, non-streaming call so it doesn't interfere with the chat.
        Routes through the small model cartridge when available — tool proposals
        are structured JSON that a small model can generate, and the agent
        reviews proposals before implementing them. Saves cloud tokens."""
        try:
            from llm_client import get_small_client_or_big

            client = get_small_client_or_big()
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
                'Respond as JSON: {"proposals": [{"name", "description", '
                '"parameters", "code_sketch"}]}'
            )
            # Use chat() not generate() — OpenAICompatibleClient only
            # exposes chat().  The old code called client.generate() which
            # exists on OllamaClient but NOT on the OpenAI backend, so
            # self_reflect was silently dead when LLM_BACKEND=openai.
            result = client.chat(
                [{"role": "user", "content": prompt}], temperature=0.4, stream=False
            )
            text = (
                result.get("response", "") if isinstance(result, dict) else str(result)
            )
            return {"reflection": text, "topic": topic}
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
                proc = _subprocess_run(
                    ["git", "checkout", "HEAD", "--", rel],
                    capture_output=True,
                    text=True,
                    cwd=str(BACKEND_ROOT),
                    timeout=10,
                )
            else:
                proc = _subprocess_run(
                    [
                        "git",
                        "checkout",
                        "HEAD",
                        "--",
                        "vaultbot_backend",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(BACKEND_ROOT),
                    timeout=10,
                )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
                "restored": rel,
            }
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return {"error": str(e)}

    # --- helpers ---------------------------------------------------------

    def _safe_name(self, name: str) -> str:
        return safe_writer.safe_name(name)

    def _resolve_path(self, file_path: str, allow_create: bool = False) -> Path | None:
        """Resolve a path relative to the vault root (delegates to safe_writer)."""
        return safe_writer.resolve_path(file_path, BACKEND_ROOT, allow_create)

    @staticmethod
    def _backup_path(target: Path) -> Path:
        """Return the backup path for a target file (delegates to safe_writer)."""
        return safe_writer.backup_path(target, BACKEND_ROOT, TRASH_DIR)
