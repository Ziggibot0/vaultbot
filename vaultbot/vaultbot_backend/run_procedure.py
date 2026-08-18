"""Synchronous CLI entrypoint for recursive procedure execution.

A procedure's code step can call ``run_procedure("Another-Procedure")``
to run another procedure as a subprocess.  Because the step-gate runtime
is async and lives in the parent process, the subprocess can't import it
directly.  Instead, the tool-injected ``run_procedure`` wrapper (see
``step_gate_runtime._build_tool_preamble``) shells out to THIS module::

    python -m run_procedure --procedure-name "X" --vault-path "..." \
        --call-stack '["Parent"]' --max-depth 3

stdout gets a single JSON object: either the ``ExecutionResult`` summary
(success) or an ``{"error": ..., "cycle_detected": true}`` /
``{"error": ..., "depth_exceeded": true}`` (loud failure).  The wrapper
parses that and either returns the child's output as a string result or
raises so the parent step fails loudly.

This module is intentionally a thin shim: all real work happens in
``step_gate_runtime.execute_procedure`` via ``asyncio.run``.  Keeping the
contract JSON-in/JSON-out matches every other code step.

See:
  - [[Procedure-Subprocess-Architecture]]
  - ``step_gate_runtime.py`` — the async runtime
  - ``procedure_compiler.py`` — compile half
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the backend dir is importable when invoked as a script.
_BACKEND = Path(__file__).parent.resolve()
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from procedure_compiler import compile_procedure
from step_gate_runtime import MAX_PROC_DEPTH, execute_procedure

# --- Stem index cache (avoids full-vault rglob on every invocation) ---
# Built once per process via a pruned os.walk (same pattern as
# procedure_tracker._iter_procedural_notes).  Keyed by vault_path.
_INDEX_CACHE: dict[str, dict[str, str]] = {}

# Reuse the same ignore set as procedure_tracker (keep in sync).
_IGNORED_DIRS = {
    ".git",
    ".obsidian",
    ".venv",
    "vaultbot_venv",
    "vaultbot_index",
    "sessions",
    "partials",
    "__pycache__",
    "trash",
}


def _get_proc_index(vault_path: str) -> dict[str, str]:
    """Build a {stem: file_path} index of procedural notes.

    Walks the vault once with a pruned os.walk, reading only frontmatter
    (the first few lines) to check for ``type: procedure``.  Far cheaper
    than rglob which walks every file and compares stems.
    """
    vault = Path(vault_path)
    index: dict[str, str] = {}
    if not vault.is_dir():
        return index
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            md = Path(root) / fname
            try:
                with open(md, encoding="utf-8", errors="replace") as f:
                    head = f.read(2048)
            except OSError:
                continue
            if not head.startswith("---"):
                continue
            end = head.find("---", 3)
            if end == -1:
                continue
            fm = head[3:end]
            if "type: procedure" not in fm:
                continue
            index[md.stem] = str(md)
    return index


def _resolve_procedure_file(proc_name: str, vault_path: str) -> Path | None:
    """Resolve a procedure note by stem using the cached index.

    Falls back to rglob if the stem isn't in the index (covers a note
    written seconds ago by a sibling subprocess that the index hasn't
    seen yet).
    """
    idx = _INDEX_CACHE.get(vault_path)
    if idx is None:
        idx = _get_proc_index(vault_path)
        _INDEX_CACHE[vault_path] = idx
    path_str = idx.get(proc_name)
    if path_str:
        return Path(path_str)
    # Fallback: rglob for a just-written note the index hasn't seen.
    vault = Path(vault_path)
    for candidate in vault.rglob("*.md"):
        if candidate.stem == proc_name:
            return candidate
    return None


def _resolve_llm_client():
    """Get the default LLM client for child procedures.

    Child procedures' LLM steps use ``get_llm_client()`` directly (see
    ``_run_llm_step``); text steps need a client with ``.chat()``.  We
    import lazily so a missing Ollama config doesn't break CLI parse.
    """
    from llm_client import get_llm_client

    return get_llm_client()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a VaultBot procedure as a subprocess (for recursion)."
    )
    parser.add_argument(
        "--procedure-name", required=True, help="Note stem of the procedure to run."
    )
    parser.add_argument("--vault-path", required=True, help="Path to the vault root.")
    parser.add_argument(
        "--call-stack",
        default="[]",
        help="JSON list of procedure names already in flight (for cycle detection).",
    )
    parser.add_argument(
        "--procedure-args",
        default="{}",
        help="JSON dict of call-time arguments forwarded to the "
        "child procedure's code steps via the injected "
        "`args` variable.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=MAX_PROC_DEPTH,
        help="Maximum recursion depth (default %(default)s).",
    )
    args = parser.parse_args()

    try:
        call_stack = json.loads(args.call_stack)
    except json.JSONDecodeError:
        call_stack = []

    try:
        procedure_args = json.loads(args.procedure_args)
        if not isinstance(procedure_args, dict):
            procedure_args = {}
    except json.JSONDecodeError:
        procedure_args = {}

    # --- Cycle detection: refuse to re-enter a procedure already running ---
    if args.procedure_name in call_stack:
        print(
            json.dumps(
                {
                    "error": f"cycle detected: {args.procedure_name} is already in "
                    f"the call stack {call_stack}",
                    "cycle_detected": True,
                    "call_stack": call_stack,
                }
            )
        )
        return 1  # non-zero exit code; JSON error is on stdout for the parent

    # --- Depth guard: cap recursion to avoid runaway token spend ---
    if len(call_stack) >= args.max_depth:
        print(
            json.dumps(
                {
                    "error": f"max procedure depth ({args.max_depth}) exceeded; "
                    f"call stack: {call_stack}",
                    "depth_exceeded": True,
                    "call_stack": call_stack,
                }
            )
        )
        return 1

    # --- Resolve the procedure note by stem (cached index, rglob fallback) ---
    vault = Path(args.vault_path)
    # Ensure VAULT_PATH is in the environment so code step subprocesses
    # (spawned by execute_procedure) can find the vault root.
    os.environ["VAULT_PATH"] = str(vault)
    proc_file = _resolve_procedure_file(args.procedure_name, str(vault))
    if not proc_file:
        print(
            json.dumps(
                {
                    "error": f"procedure not found: {args.procedure_name}",
                }
            )
        )
        return 1

    proc = compile_procedure(str(proc_file))
    if proc is None:
        print(
            json.dumps(
                {
                    "error": f"not a procedure note: {args.procedure_name}",
                }
            )
        )
        return 1

    # --- Execute via the async runtime ---
    # Read the procedure's model_cartridge to select the right LLM client.
    # Falls back to 'big' if the field is missing or empty.
    #
    # Only resolve a client when the procedure actually has an LLM/text
    # step. A pure code-step procedure (e.g. one that just calls
    # run_procedure to recurse) needs no model — resolving one
    # unconditionally breaks in environments with no configured model
    # (CI, fresh installs) and violates the "no I/O" unit-test contract.
    llm_client = None
    _needs_llm = any(s.step_type in ("llm", "text") for s in proc.steps)
    if _needs_llm:
        try:
            from llm_client import get_cartridge

            cartridge = getattr(proc, "model_cartridge", None) or "big"
            llm_client = get_cartridge(cartridge)
            if llm_client is None:
                # Cartridge not assigned — fall back to big
                from llm_client import get_llm_client

                llm_client = get_llm_client()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            print(
                json.dumps(
                    {
                        "error": f"LLM client unavailable: {e}",
                    }
                )
            )
            return 1

    # --- Procedure tracker: log this child's pass/fail + step results --- #
    # When invoked as a sub-procedure (via run_procedure() in a parent's
    # code step), the parent forwards its tracker log path via the
    # PROCEDURE_TRACKER_LOG env var. We instantiate a ProcedureTracker
    # pointed at the SAME log file so the child's execution is graded too
    # — including step-level results. Without this, sub-procedures are
    # invisible to the grading loop. See PROCEDURE_FIRST design.
    procedure_tracker = None
    _tracker_log = os.environ.get("PROCEDURE_TRACKER_LOG", "")
    if _tracker_log:
        try:
            from procedure_tracker import ProcedureTracker

            procedure_tracker = ProcedureTracker(
                log_path=_tracker_log, vault_path=args.vault_path
            )
        except Exception as e:  # noqa: BLE001 — best-effort; grading is a bonus
            # Don't let tracker init failure break the procedure run.
            print(
                json.dumps(
                    {
                        "warning": f"procedure_tracker init failed: {e}",
                    }
                ),
                file=sys.stderr,
            )

    try:
        result = asyncio.run(
            execute_procedure(
                procedure=proc,
                context="",
                llm_client=llm_client,
                vault_path=args.vault_path,
                call_stack=call_stack + [args.procedure_name],
                procedure_args=procedure_args,
                procedure_tracker=procedure_tracker,
            )
        )
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        print(
            json.dumps(
                {
                    "error": f"runtime error: {e}",
                    "traceback": __import__("traceback").format_exc(),
                }
            )
        )
        return 1

    # --- Serialise the ExecutionResult for the parent subprocess ---
    out = {
        "procedure": result.procedure_name,
        "overall_passed": result.overall_passed,
        "failed_step": result.failed_step,
        "steps_executed": len(result.steps),
        "final_output": result.final_output[:4000],
        "child_procedures": result.child_procedures,
        "step_details": [
            {
                "step": sr.step_number,
                "type": sr.step_type,
                "passed": sr.passed,
                "error": sr.error or sr.validation_error,
            }
            for sr in result.steps
        ],
    }
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
