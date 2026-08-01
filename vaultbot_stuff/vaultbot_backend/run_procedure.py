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
import sys
from pathlib import Path

# Ensure the backend dir is importable when invoked as a script.
_BACKEND = Path(__file__).parent.resolve()
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from procedure_compiler import compile_procedure
from step_gate_runtime import MAX_PROC_DEPTH, execute_procedure


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
        description="Run a VaultBot procedure as a subprocess (for recursion).")
    parser.add_argument("--procedure-name", required=True,
                        help="Note stem of the procedure to run.")
    parser.add_argument("--vault-path", required=True,
                        help="Path to the vault root.")
    parser.add_argument("--call-stack", default="[]",
                        help="JSON list of procedure names already in flight "
                             "(for cycle detection).")
    parser.add_argument("--max-depth", type=int, default=MAX_PROC_DEPTH,
                        help="Maximum recursion depth (default %(default)s).")
    args = parser.parse_args()

    try:
        call_stack = json.loads(args.call_stack)
    except json.JSONDecodeError:
        call_stack = []

    # --- Cycle detection: refuse to re-enter a procedure already running ---
    if args.procedure_name in call_stack:
        print(json.dumps({
            "error": f"cycle detected: {args.procedure_name} is already in "
                     f"the call stack {call_stack}",
            "cycle_detected": True,
            "call_stack": call_stack,
        }))
        return 0  # zero exit code so the parent step sees the JSON error

    # --- Depth guard: cap recursion to avoid runaway token spend ---
    if len(call_stack) >= args.max_depth:
        print(json.dumps({
            "error": f"max procedure depth ({args.max_depth}) exceeded; "
                     f"call stack: {call_stack}",
            "depth_exceeded": True,
            "call_stack": call_stack,
        }))
        return 0

    # --- Resolve the procedure note by stem ---
    proc_file = None
    vault = Path(args.vault_path)
    for candidate in vault.rglob("*.md"):
        if candidate.stem == args.procedure_name:
            proc_file = candidate
            break
    if not proc_file:
        print(json.dumps({
            "error": f"procedure not found: {args.procedure_name}",
        }))
        return 0

    proc = compile_procedure(str(proc_file))
    if proc is None:
        print(json.dumps({
            "error": f"not a procedure note: {args.procedure_name}",
        }))
        return 0

    # --- Execute via the async runtime ---
    try:
        llm_client = _resolve_llm_client()
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        print(json.dumps({
            "error": f"LLM client unavailable: {e}",
        }))
        return 0

    try:
        result = asyncio.run(execute_procedure(
            procedure=proc,
            context="",
            llm_client=llm_client,
            vault_path=args.vault_path,
            call_stack=call_stack + [args.procedure_name],
        ))
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        print(json.dumps({
            "error": f"runtime error: {e}",
            "traceback": __import__("traceback").format_exc(),
        }))
        return 0

    # --- Serialise the ExecutionResult for the parent subprocess ---
    out = {
        "procedure": result.procedure_name,
        "overall_passed": result.overall_passed,
        "failed_step": result.failed_step,
        "steps_executed": len(result.steps),
        "final_output": result.final_output[:4000],
        "child_procedures": result.child_procedures,
        "step_details": [
            {"step": sr.step_number, "type": sr.step_type,
             "passed": sr.passed,
             "error": sr.error or sr.validation_error}
            for sr in result.steps
        ],
    }
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
