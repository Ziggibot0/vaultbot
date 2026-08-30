"""Step executors for procedure code and LLM steps.

Two of the three procedure step types live here:

1. **Code steps** (``step_type == "code"``): Python code blocks that
   run in a subprocess with scoped tool injection. Zero LLM cost. The
   subprocess receives prior step results as an environment variable and
   returns its result as JSON on stdout. Loud failures include the full
   traceback.

2. **LLM steps** (``step_type == "llm"``): ``[llm:]`` tags that compile
   to a stripped-down LLM call via ``get_llm_client()``. Minimal context
   — only prior step results + the instruction, not VaultBot's full
   system prompt + vault context. The procedure-bot is NOT VaultBot.

The third step type (v1 text steps) stays in ``step_gate_runtime.py``
because it needs the active-frame builder which references the
``Procedure`` overview — keeping it here would create a circular import
with the orchestrator.

Tool injection for code steps is driven by the procedure's
``allowed_tools`` frontmatter field. Only the listed tools are injected
into the subprocess namespace. This is the permission scope — a
procedure that verifies claims gets ``vault_search`` and
``llm_generate``, not ``safe_write`` or ``vault_delete``.

See:
  - ``step_gate_runtime.py`` — the orchestrator that calls these
  - ``procedure_tool_preamble.py`` — ``_build_tool_preamble``
  - ``procedure_compiler.py`` — the ``Step`` dataclass
  - [[Procedure-Subprocess-Architecture]]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from procedure_compiler import Step
from procedure_tool_preamble import _build_tool_preamble
from subprocess_utils import preexec_fn, scrubbed_env
from subprocess_utils import run as _subprocess_run

# ── Subprocess wrapper for code steps ───────────────────────────────────


def _run_code_step(
    step: Step,
    allowed_tools: list[str],
    vault_path: str,
    prior_results: dict[float, str],
    timeout: int = 120,
    procedure_name: str = "",
    call_stack: list[str] | None = None,
    model_cartridge: str = "big",
    procedure_args: dict | None = None,
    procedures_index: list[dict] | None = None,
) -> tuple[bool, str, str | None, str | None, dict]:
    """Execute a code step in a subprocess.

    Returns ``(success, output, error, traceback, sub_prior)`` where
    ``sub_prior`` is the subprocess's ``prior_results`` dict (may contain
    step-added keys like "scan", "analyze", "telemetry" that the caller
    merges back into the shared ``prior_results``).

    ``procedure_name`` and ``call_stack`` are passed to the subprocess
    so the injected ``run_procedure`` tool can detect cycles and enforce
    MAX_PROC_DEPTH when this step recurses into another procedure.

    ``procedures_index`` is the runtime's compact procedure library
    (name/description/when_to_use/status per procedure), injected into the
    step namespace as ``procedures_index`` so meta-procedures (e.g.
    Small-Model-Route) read the library from the runtime instead of
    globbing the vault for a hardcoded directory path.
    """
    if step.code is None:
        return False, "", "code step has no code", "", {}

    tool_preamble = _build_tool_preamble(allowed_tools)

    # Build the wrapper script using string replacement (not .format()
    # to avoid conflicts with { and } in Python code).
    wrapper = (
        "import sys, json, os, traceback\n"
        "from pathlib import Path\n"
        "\n"
        'vault_path = os.environ.get("VAULT_PATH", ".")\n'
        "FRAMEWORK_ROOT = os.environ.get(\n"
        '    "FRAMEWORK_ROOT", os.path.dirname(vault_path))\n'
        'prior_results = json.loads(os.environ.get("PRIOR_RESULTS", "{}"))\n'
        'allowed = json.loads(os.environ.get("PROCEDURE_ALLOWED_TOOLS", "[]"))\n'
        'procedure_args = json.loads(os.environ.get("PROCEDURE_ARGS", "{}"))\n'
        'procedures_index = json.loads(os.environ.get("PROCEDURES_INDEX", "[]"))\n'
        'procedure_name = os.environ.get("PROCEDURE_SELF_NAME", "")\n'
        '_IGNORED_DIRS = {".git", ".obsidian", ".venv", "vaultbot_venv", '
        '"vaultbot_index", "sessions", "partials", "__pycache__"}\n'
        "\n"
        "namespace = {\n"
        '    "__builtins__": __builtins__,\n'
        '    "prior_results": prior_results,\n'
        '    "procedure_args": procedure_args,\n'
        '    "args": procedure_args,\n'
        '    "procedures_index": procedures_index,\n'
        '    "procedure_name": procedure_name,\n'
        '    "Path": Path,\n'
        '    "json": json,\n'
        '    "os": os,\n'
        '    "vault_path": vault_path,\n'
        '    "FRAMEWORK_ROOT": FRAMEWORK_ROOT,\n'
        '    "_IGNORED_DIRS": _IGNORED_DIRS,\n'
        "}\n"
        "\n"
        "# --- Tool injection ---\n" + tool_preamble + "\n"
        "# --- Step code ---\n"
        "step_code = " + repr(step.code) + "\n"
        "\n"
        "try:\n"
        "    exec(step_code, namespace)\n"
        '    result = namespace.get("result")\n'
        '    if result is None and "result" not in namespace:\n'
        '        result = ""\n'
        "    try:\n"
        "        json.dumps(result)\n"
        "    except (TypeError, ValueError):\n"
        "        result = str(result)\n"
        "    # Return prior_results so the runtime can merge step-added keys\n"
        '    # (e.g. "scan", "analyze", "telemetry") back into the shared dict.\n'
        '    _pr = namespace.get("prior_results", {})\n'
        "    if not isinstance(_pr, dict):\n"
        "        _pr = {}\n"
        '    print(json.dumps({"status": "ok", "result": result, '
        '"prior_results": _pr}))\n'
        "except Exception as e:  # noqa: BLE001 — best-effort, returns "
        "error to caller\n"
        "    print(json.dumps({\n"
        '        "status": "error",\n'
        '        "error": str(e),\n'
        '        "traceback": traceback.format_exc(),\n'
        "    }))\n"
    )

    # Find the venv python
    backend_dir = Path(__file__).parent.resolve()
    venv_python = str(backend_dir.parent / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_python).exists():
        venv_python = sys.executable

    # Prepare environment — scrubbed of secrets (API keys/tokens/passwords)
    # so LLM-authored procedure code cannot read or exfiltrate them. Only the
    # non-secret PROCEDURE_* overrides and PYTHONPATH/VAULT_PATH are added back.
    # FRAMEWORK_ROOT is the repo root (parent of the vault): procedure code
    # steps that need backend source paths (``vaultbot_backend/…``) resolve
    # them against FRAMEWORK_ROOT, since those paths never lived inside the
    # vault. See paths.py for the two-root layout.
    env = {
        **scrubbed_env(),
        "PYTHONPATH": str(backend_dir),
        "VAULT_PATH": vault_path,
        "FRAMEWORK_ROOT": str(backend_dir.parent.resolve()),
        "PROCEDURE_ALLOWED_TOOLS": json.dumps(allowed_tools),
        "PROCEDURES_INDEX": json.dumps(procedures_index or [], default=str),
        "PRIOR_RESULTS": json.dumps(prior_results, default=str),
        "PROCEDURE_SELF_NAME": procedure_name,
        "PROCEDURE_CALL_STACK": json.dumps(call_stack or []),
        "PROCEDURE_MODEL_CARTRIDGE": model_cartridge,
        "PROCEDURE_ARGS": json.dumps(procedure_args or {}, default=str),
    }
    # Forward the procedure-tracker log path so child procedures (run via
    # the injected run_procedure tool → run_procedure.py) can instantiate
    # their OWN ProcedureTracker pointed at the SAME log file. This is what
    # makes sub-procedures log their own pass/fail + step results: without
    # it, only the top-level procedure run from chat_handler logs, and a
    # procedure called by another procedure is invisible to the grading
    # loop. See PROCEDURE_FIRST design (2026-08-04).
    _tracker_log = os.environ.get("PROCEDURE_TRACKER_LOG", "")
    if _tracker_log:
        env["PROCEDURE_TRACKER_LOG"] = _tracker_log

    try:
        proc = _subprocess_run(
            [venv_python, "-c", wrapper],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(vault_path).resolve()),
            env=env,
            # Resource limits (POSIX): mem/CPU/fork caps. None on Windows.
            preexec_fn=preexec_fn,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"subprocess timeout after {timeout}s", "", {}
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False, "", f"subprocess error: {e}", traceback.format_exc(), {}

    # Parse output.  The wrapper always prints the envelope as the LAST
    # line of stdout.  Step code may print debug output before it (e.g.
    # ``print(result)`` in Authority-Check).  We take only the last line
    # so debug prints don't corrupt the envelope parse.
    stdout = proc.stdout.strip()
    if not stdout:
        stderr = proc.stderr.strip()[:2000]
        return False, "", f"no stdout from subprocess. stderr: {stderr}", "", {}

    # Take the last non-empty line as the envelope
    lines = [line for line in stdout.split("\n") if line.strip()]
    envelope_line = lines[-1] if lines else stdout

    try:
        result = json.loads(envelope_line)
    except json.JSONDecodeError:
        return (
            False,
            stdout[:2000],
            f"invalid JSON from subprocess: {envelope_line[:200]}",
            "",
            {},
        )

    if result.get("status") == "error":
        return (
            False,
            "",
            result.get("error", "unknown error"),
            result.get("traceback", ""),
            {},
        )

    output = result.get("result", "")
    if not isinstance(output, str):
        output = json.dumps(output, default=str)

    # Merge subprocess prior_results back — step code may have added
    # keys like "scan", "analyze", "telemetry" that must survive into
    # the next step's PRIOR_RESULTS env var.
    sub_prior = result.get("prior_results", {})
    if not isinstance(sub_prior, dict):
        sub_prior = {}

    return True, output, None, None, sub_prior


# ── LLM step execution ──────────────────────────────────────────────────


def _run_llm_step(
    step: Step,
    prior_results: dict[float, str],
    llm_client: Any = None,
    procedure_name: str = "",
) -> tuple[bool, str, str | None]:
    """Execute an LLM step via the cartridge-selected client with minimal context.

    Returns ``(success, output, error)``.

    The LLM gets:
    - System: "You are a procedure executor. Follow the instruction."
    - Prompt: prior step results + the LLM instruction

    No vault context, no system prompt, no identity — the procedure-bot
    is NOT VaultBot.

    Args:
        llm_client: The cartridge-selected LLM client (big/small/vision).
            If None, falls back to get_llm_client() (the big model).
    """
    if step.llm_instruction is None:
        return False, "", "LLM step has no instruction"

    # Build minimal context from prior results
    prior_context = ""
    if prior_results:
        prior_lines = []
        for num, out in prior_results.items():
            snippet = out[:2000] + ("..." if len(out) > 2000 else "")
            prior_lines.append(f"Step {num} output:\n{snippet}")
        prior_context = "\n\n".join(prior_lines)

    prompt_parts = []
    if prior_context:
        prompt_parts.append("## Prior Step Results\n")
        prompt_parts.append(prior_context)
        prompt_parts.append("\n\n---\n\n")
    prompt_parts.append(step.llm_instruction)

    prompt = "\n".join(prompt_parts)

    system = (
        "You are a procedure executor. Follow the instruction exactly. "
        "Output only the result. Do not add commentary or explanation "
        "unless the instruction asks for it."
    )

    try:
        client = llm_client
        if client is None:
            from llm_client import get_llm_client

            client = get_llm_client()
        # Use chat() — the unified LLM client contract (see llm_client.py).
        # Both OllamaClient and OpenAICompatibleClient expose chat(), not generate().
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        set_context = getattr(client, "set_invocation_context", None)
        if callable(set_context):
            set_context(
                purpose="procedure_step",
                procedure=procedure_name,
                step=step.number,
            )
        result = client.chat(
            messages=messages,
            stream=False,
            think=False,
        )
        output = result.get("response", "")
        if not output:
            return False, "", "LLM returned empty response"
        return True, output, None
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False, "", f"LLM error: {e}"
