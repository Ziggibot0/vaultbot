"""Step-Gate Runtime — execute compiled procedures step by step.

This is the "execute" half of the compile-then-execute pattern (see
[[Procedural-Bootstrap-and-Evolution-Plan]] and
[[Procedure-Subprocess-Architecture]]).

Three step types are supported: **code** (v2, subprocess + tool
injection, zero LLM cost), **llm** (v2, stripped-down LLM call), and
**text** (v1, active-frame LLM call, backward compat).

This module is the orchestrator: it owns the dataclasses
(``StepResult`` / ``ExecutionResult``), the v1 active-frame builder, and
the main ``execute_procedure`` loop.  Validation/condition logic,
code/LLM step execution, and the tool-preamble generator have been
extracted into focused modules and are re-imported here so existing
``from step_gate_runtime import ...`` callers (and tests) keep working.

See:
  - ``procedure_compiler.py`` — the compile half
  - ``procedure_validators.py`` — validation + condition evaluation
  - ``procedure_step_executor.py`` — code/LLM step execution
  - ``procedure_tool_preamble.py`` — tool preamble builder
  - ``procedure_tracker.py`` — pass/fail logging (step-level via
    ``log_step_result``)
  - [[Procedural-Bootstrap-and-Evolution-Plan]]
  - [[Procedure-Subprocess-Architecture]]
  - [[Deterministic-Scaffolding-for-Small-Models]]
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from procedure_compiler import Procedure, Step
from procedure_step_executor import _run_code_step, _run_llm_step
from procedure_tool_preamble import _build_tool_preamble  # noqa: F401 — re-export

# Re-export helpers from extracted modules so existing
# ``from step_gate_runtime import ...`` callers (and tests) keep working.
from procedure_validators import (  # noqa: F401 — re-exported for tests/callers
    _count_thing,
    _evaluate_condition,
    _parse_validation,
    _validate_step,
)

# ── Data structures ───────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Outcome of executing a single step.

    Attributes:
        step_number: Which step was executed.
        step_type: "code", "llm", or "text".
        passed: Whether the output passed validation.
        output: The step's output (LLM text, code result, or text output).
        validation_error: If validation failed, what was missing (None if passed).
        error: If the step crashed, the error message (None on success).
        traceback: Full traceback if the step crashed (None on success).
    """

    step_number: float
    step_type: str
    passed: bool
    output: str
    validation_error: str | None = None
    error: str | None = None
    traceback: str | None = None


@dataclass
class ExecutionResult:
    """Outcome of executing an entire procedure.

    Attributes:
        procedure_name: Name of the procedure that was executed.
        steps: Per-step results, in execution order.
        overall_passed: True if every step passed validation.
        final_output: Concatenation of all step outputs (the complete answer).
        failed_step: Step number that caused the procedure to stop, or None.
    """

    procedure_name: str
    steps: list[StepResult]
    overall_passed: bool
    final_output: str
    failed_step: float | None = None
    child_procedures: list[dict] = field(default_factory=list)
    # Each entry: {"name": str, "overall_passed": bool,
    # "steps_executed": int}. Populated when a step invokes
    # ``run_procedure`` to run another procedure recursively.


def _build_active_frame(
    step: Step,
    procedure: Procedure,
    context: str,
    step_outputs: list[tuple[float, str]],
) -> list[dict[str, str]]:
    """Build the active frame for the LLM (v1 text steps).

    The active frame puts the CURRENT STEP FIRST (checkpointing —
    resets evidence distance), then prior step outputs, then the full
    procedure overview (full-program cursor), then the vault context.
    """
    overview_lines = []
    for s in procedure.steps:
        marker = " >>> " if s.number == step.number else "     "
        overview_lines.append(f"{marker}Step {s.number}: {s.instruction}")
    overview = "\n".join(overview_lines)

    prior_outputs = ""
    if step_outputs:
        prior_lines = []
        for num, out in step_outputs:
            snippet = out[:500] + ("..." if len(out) > 500 else "")
            prior_lines.append(f"Step {num} output: {snippet}")
        prior_outputs = "\n\n".join(prior_lines)

    prompt_parts = [
        f"## CURRENT STEP (Step {step.number})",
        f"{step.instruction}",
        "",
    ]

    if step.validation:
        prompt_parts.append(f"Validation criteria: {step.validation}")
        prompt_parts.append("")

    if prior_outputs:
        prompt_parts.append("## PRIOR STEP OUTPUTS")
        prompt_parts.append(prior_outputs)
        prompt_parts.append("")

    prompt_parts.append("## FULL PROCEDURE OVERVIEW")
    prompt_parts.append(overview)
    prompt_parts.append("")

    if context:
        prompt_parts.append("## VAULT CONTEXT")
        prompt_parts.append(context)
        prompt_parts.append("")

    prompt_parts.append(
        "Execute the current step. Output only the result of this step."
    )

    user_content = "\n".join(prompt_parts)

    system_content = (
        "You are VaultBot executing a procedure step-by-step. "
        "Follow the current step exactly. Do not skip ahead. "
        "Do not combine steps. Output only what the current step asks for."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ── Step-gate runtime ────────────────────────────────────────────────────

# Maximum recursion depth for procedures calling procedures via
# run_procedure.  3 lets a verify-procedure call a source-credibility
# procedure without runaway token spend.  See [[Procedure-Subprocess-Architecture]].
MAX_PROC_DEPTH = 3

# Diagnosis message emitted when a procedure compiles zero steps due to a
# format mismatch (body has content but no recognised step markers).
_ZERO_STEPS_DIAGNOSIS = (
    "PROCEDURE COMPILED 0 STEPS. The procedure compiler "
    "(procedure_compiler.py _parse_steps) recognizes two formats "
    "inside a ## Steps section:\n"
    "  ### Step N: short summary\n"
    "  ```python\n"
    "  code here\n"
    "  ```\n"
    "\n"
    "  or the legacy numbered format:\n"
    "  1. ```python\n"
    "     code here\n"
    "     ```\n"
    "\n"
    "Both require either a numbered 'N.' line or a '### Step N:' "
    "header followed by a ```python fence. Check the procedure's "
    "## Steps section format."
)


def _empty_procedure_result(
    procedure: Procedure,
    session_logger: Any,
) -> ExecutionResult:
    """Return an ``ExecutionResult`` for a procedure with zero steps.

    Distinguishes two cases:
    1. Empty body (no content after ## Steps) → legitimately 0 steps, pass.
    2. Body has content but 0 parsed steps → format mismatch, FAIL with a
       loud diagnosis telling the caller exactly what went wrong.
    """
    _body = (procedure.raw_text or "").strip()
    # Strip frontmatter to check the actual body content.
    if _body.startswith("---"):
        _fm_end = _body.find("\n---", 3)
        if _fm_end > 0:
            _body = _body[_fm_end + 4 :].strip()
    # Strip the ## Steps header line itself — what matters is whether
    # there's content UNDER it, not the header itself.
    import re as _re

    _steps_match = _re.search(r"^##\s+Steps\s*$", _body, _re.MULTILINE | _re.IGNORECASE)
    if _steps_match:
        _body = _body[_steps_match.end() :].strip()
    if not _body:
        # Empty body — legitimately 0 steps.
        return ExecutionResult(
            procedure_name=procedure.name,
            steps=[],
            overall_passed=True,
            final_output="",
        )
    # Body has content but 0 parsed steps — loud failure with diagnosis.
    _body_snippet = (procedure.raw_text or "")[:200]
    if session_logger:
        session_logger.log(
            "procedure_zero_steps",
            {
                "procedure": procedure.name,
                "diagnosis": _ZERO_STEPS_DIAGNOSIS,
                "body_snippet": _body_snippet,
            },
        )
    return ExecutionResult(
        procedure_name=procedure.name,
        steps=[],
        overall_passed=False,
        final_output=_ZERO_STEPS_DIAGNOSIS,
        failed_step=0,
    )


def _truncate_traceback(tb: str, max_lines: int = 40) -> str:
    """Keep only the last ``max_lines`` lines of a traceback string.

    The part with the actual failing line + exception, not the whole
    subprocess wrapper preamble.
    """
    _tb_lines = tb.strip().splitlines()
    if len(_tb_lines) > max_lines:
        return "\n".join(_tb_lines[-max_lines:])
    return tb


def _next_step_num(
    step_map: dict[float, Step],
    current: float,
) -> float | None:
    """Return the step number after ``current`` in sorted order, or None."""
    step_numbers = sorted(step_map.keys())
    idx = step_numbers.index(current)
    return step_numbers[idx + 1] if idx + 1 < len(step_numbers) else None


def _build_proc_error_details(
    failed_step: float | None,
    step_results: list[StepResult],
) -> str:
    """Build the enriched error_details string for procedure-level logging."""
    if not failed_step:
        return ""
    details = f"failed at step {failed_step}"
    failed_sr = next((r for r in step_results if r.step_number == failed_step), None)
    if failed_sr is not None:
        sr_err = failed_sr.error or failed_sr.validation_error or ""
        if sr_err:
            details += f": {sr_err}"
        if failed_sr.traceback:
            details += "\n" + _truncate_traceback(failed_sr.traceback)
    return details


async def execute_procedure(
    procedure: Procedure,
    context: str,
    llm_client: Any,
    vault_path: str = ".",
    session_logger: Any = None,
    progress_callback: Callable | None = None,
    procedure_tracker: Any = None,
    call_stack: list[str] | None = None,
    procedure_args: dict | None = None,
) -> ExecutionResult:
    """Execute a compiled procedure one step at a time with gating.

    Handles all three step types: code, llm, text (see module docstring).

    The runtime never raises — errors are captured in StepResult and
    the procedure stops gracefully with a loud failure report.

    Args:
        procedure: Compiled Procedure object from procedure_compiler.
        context: Vault context string (used only for v1 text steps).
        llm_client: Main LLM client (used only for v1 text steps).
        vault_path: Path to the vault root (used for tool injection).
        session_logger: Optional session logger for structured logging.
        progress_callback: Optional async callback ``(step_number,
            total_steps, output, instruction, step_type, status)`` for
            progress updates (called before/after each step).
        procedure_tracker: Optional ProcedureTracker for step-level logging.
        call_stack: Procedure names already in flight (cycle detection
            for recursive ``run_procedure``). See [[Procedure-Subprocess-Architecture]].
        procedure_args: Call-time args forwarded to code steps via the
            injected ``args`` variable (env var ``PROCEDURE_ARGS``).
    """
    call_stack = list(call_stack or [])
    if not procedure.steps:
        return _empty_procedure_result(procedure, session_logger)

    step_results: list[StepResult] = []
    step_outputs: list[tuple[float, str]] = []
    all_outputs: list[str] = []
    prior_results: dict[float, str] = {}

    # Build step lookup map
    step_map = {s.number: s for s in procedure.steps}
    executed_steps: set[int] = set()
    current_step_num = procedure.steps[0].number
    max_iterations = len(procedure.steps) * 3
    failed_step: int | None = None
    child_procedures: list[dict] = []

    iterations = 0
    while current_step_num is not None and iterations < max_iterations:
        iterations += 1

        if current_step_num in executed_steps:
            break

        step = step_map.get(current_step_num)
        if step is None:
            break

        executed_steps.add(current_step_num)

        # Capture the start time for elapsed-time reporting.
        _step_start_t = time.time()

        # Build an input preview from prior step results for the UI.
        _input_preview = ""
        if prior_results:
            _last_prior = list(prior_results.values())[-1] if prior_results else ""
            if isinstance(_last_prior, str):
                _input_preview = _last_prior[:500]
            else:
                try:
                    _input_preview = json.dumps(_last_prior, default=str)[:500]
                except Exception:  # noqa: BLE001
                    _input_preview = str(_last_prior)[:500]

        if progress_callback:
            await progress_callback(
                step.number,
                len(procedure.steps),
                "",
                step.instruction[:200],
                step.step_type,
                "running",
                input_preview=_input_preview,
            )

        # --- Condition gate: skip if precondition fails (fail-safe). ---
        if step.condition is not None:
            should_run, reason = _evaluate_condition(
                step.condition, prior_results, step_outputs
            )
            if not should_run:
                sr = StepResult(
                    step_number=step.number,
                    step_type=step.step_type,
                    passed=True,  # skipped ≠ failed
                    output=f"[skipped: condition '{reason}' not met]",
                )
                step_results.append(sr)
                step_outputs.append((step.number, sr.output))
                if session_logger:
                    session_logger.log(
                        "step_gate_condition_skip",
                        {
                            "procedure": procedure.name,
                            "step": step.number,
                            "condition": step.condition,
                            "reason": reason,
                        },
                    )
                # Skip to next step without executing.
                current_step_num = _next_step_num(step_map, current_step_num)
                continue

        # --- Execute based on step type ---
        if step.step_type == "code":
            _step_timeout = 300 if "llm_generate" in procedure.allowed_tools else 120
            # to_thread keeps the event loop unblocked so asyncio.wait_for
            # timeouts (e.g. Think preflight in chat_handler.py) can fire.
            success, output, error, tb, sub_prior = await asyncio.to_thread(
                _run_code_step,
                step,
                procedure.allowed_tools,
                vault_path,
                prior_results,
                timeout=_step_timeout,
                procedure_name=procedure.name,
                call_stack=call_stack,
                model_cartridge=getattr(procedure, "model_cartridge", "big"),
                procedure_args=procedure_args,
            )
            if success:
                # Merge subprocess prior_results back (step-added keys
                # like "scan", "analyze", "telemetry" survive to next step).
                if sub_prior:
                    prior_results.update(sub_prior)
                # Capture child procedures the step spawned via run_procedure.
                try:
                    parsed = (
                        json.loads(output) if output.strip().startswith("{") else None
                    )
                    if isinstance(parsed, dict):
                        for child in parsed.get("child_procedures", []):
                            if isinstance(child, dict) and child.get("name"):
                                child_procedures.append(child)
                except (json.JSONDecodeError, AttributeError):
                    pass
                sr = StepResult(
                    step_number=step.number,
                    step_type="code",
                    passed=True,
                    output=output,
                )
            else:
                sr = StepResult(
                    step_number=step.number,
                    step_type="code",
                    passed=False,
                    output="",
                    error=error,
                    traceback=tb,
                )
                step_results.append(sr)
                failed_step = step.number
                if session_logger:
                    session_logger.log(
                        "step_gate_code_error",
                        {
                            "procedure": procedure.name,
                            "step": step.number,
                            "error": error,
                            "traceback": tb[:500] if tb else "",
                        },
                    )
                break

        elif step.step_type == "llm":
            # to_thread keeps the event loop unblocked (see code step above).
            success, output, error = await asyncio.to_thread(
                _run_llm_step, step, prior_results, llm_client, procedure_args
            )
            if success:
                sr = StepResult(
                    step_number=step.number,
                    step_type="llm",
                    passed=True,
                    output=output,
                )
            else:
                sr = StepResult(
                    step_number=step.number,
                    step_type="llm",
                    passed=False,
                    output="",
                    error=error,
                )
                step_results.append(sr)
                failed_step = step.number
                if session_logger:
                    session_logger.log(
                        "step_gate_llm_error",
                        {
                            "procedure": procedure.name,
                            "step": step.number,
                            "error": error,
                        },
                    )
                break

        else:  # text step (v1)
            messages = _build_active_frame(step, procedure, context, step_outputs)
            try:
                # to_thread keeps the event loop unblocked (see above).
                result = await asyncio.to_thread(
                    llm_client.chat,
                    messages,
                    temperature=0.3,
                    stream=False,
                    think=False,
                )
                output = result.get("response", "")
                passed, val_error = _validate_step(output, step.validation)
                sr = StepResult(
                    step_number=step.number,
                    step_type="text",
                    passed=passed,
                    output=output,
                    validation_error=val_error,
                )
                # Validation failure stops the procedure loudly.
                if not passed:
                    step_results.append(sr)
                    failed_step = step.number
                    if session_logger:
                        session_logger.log(
                            "step_gate_validation_fail",
                            {
                                "procedure": procedure.name,
                                "step": step.number,
                                "validation_error": val_error,
                            },
                        )
                    break
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                sr = StepResult(
                    step_number=step.number,
                    step_type="text",
                    passed=False,
                    output="",
                    error=f"LLM error: {e}",
                )
                step_results.append(sr)
                failed_step = step.number
                if session_logger:
                    session_logger.log(
                        "step_gate_llm_error",
                        {
                            "procedure": procedure.name,
                            "step": step.number,
                            "error": str(e),
                        },
                    )
                break

        step_results.append(sr)
        step_outputs.append((step.number, sr.output))
        all_outputs.append(sr.output)
        prior_results[step.number] = sr.output

        # Step-level logging
        if procedure_tracker:
            try:
                # Enrich with full debug info (error + truncated traceback)
                # so the vault can self-heal. See PROCEDURE_FIRST design.
                _step_err = sr.error or sr.validation_error or ""
                if sr.traceback:
                    _tb = _truncate_traceback(sr.traceback)
                    _step_err = (_step_err + "\n" + _tb).strip() if _step_err else _tb
                procedure_tracker.log_step_result(
                    procedure.name,
                    step.number,
                    sr.passed,
                    _step_err,
                )
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass

        if session_logger:
            session_logger.log(
                "step_gate_result",
                {
                    "procedure": procedure.name,
                    "step": step.number,
                    "step_type": sr.step_type,
                    "passed": sr.passed,
                    "error": sr.error or "",
                    "output_length": len(sr.output),
                },
            )

        _elapsed_s = round(time.time() - _step_start_t, 1)

        if progress_callback:
            await progress_callback(
                step.number,
                len(procedure.steps),
                sr.output,
                step.instruction[:200],
                step.step_type,
                "passed" if sr.passed else "failed",
                input_preview=_input_preview,
                elapsed_s=_elapsed_s,
                error=sr.error or sr.validation_error or "",
            )

        # --- Branch jump: if the step has a branch_target and passed. ---
        # The ``executed_steps`` set + ``max_iterations`` guard prevents
        # infinite loops on a branch cycle.
        if step.branch_target is not None and sr.passed:
            target = step.branch_target
            if target in step_map:
                if session_logger:
                    session_logger.log(
                        "step_gate_branch",
                        {
                            "procedure": procedure.name,
                            "from_step": step.number,
                            "to_step": target,
                        },
                    )
                current_step_num = target
                continue
            # Branch target doesn't exist — log loudly and fall through.
            if session_logger:
                session_logger.log(
                    "step_gate_branch_missing",
                    {
                        "procedure": procedure.name,
                        "from_step": step.number,
                        "to_step": target,
                    },
                )

        # Advance to next step
        current_step_num = _next_step_num(step_map, current_step_num)

    overall_passed = all(r.passed for r in step_results) if step_results else True
    final_output = "\n\n".join(all_outputs)

    if session_logger:
        session_logger.log(
            "step_gate_complete",
            {
                "procedure": procedure.name,
                "steps_executed": len(step_results),
                "overall_passed": overall_passed,
                "failed_step": failed_step,
                "final_output_length": len(final_output),
            },
        )

    # Procedure-level logging
    if procedure_tracker:
        with contextlib.suppress(Exception):
            procedure_tracker.log_result(
                procedure=procedure.name,
                task="procedure_execution",
                validation_result="pass" if overall_passed else "fail",
                validation_tool="step_gate",
                error_details=_build_proc_error_details(failed_step, step_results),
                category="validation_error",
            )

    return ExecutionResult(
        procedure_name=procedure.name,
        steps=step_results,
        overall_passed=overall_passed,
        final_output=final_output,
        failed_step=failed_step,
        child_procedures=child_procedures,
    )
