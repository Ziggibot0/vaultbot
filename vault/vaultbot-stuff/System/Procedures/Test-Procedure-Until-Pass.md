---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Test a procedure by running it, and if it fails, automatically diagnose and fix it, then re-run — iterating until the procedure passes or a max retry cap is hit. This is the automated procedure-validation loop: it composes execute (run_procedure), diagnose+fix (Procedure-Fixer), and re-test into a single self-healing cycle. Use this when you create a new procedure, when you want to verify a procedure actually works, or when the user says 'test this procedure' or 'make sure this procedure works'. Pass procedure_name (the target) and any args the target needs via the args object."
when_to_use: when testing a new procedure, when asked to verify a procedure works, when a procedure was just created and needs validation, when the user says 'test this procedure', when you want to auto-fix a failing procedure
falsifiable_if: the target procedure passes after the loop but produces wrong output, or the loop reports success when the procedure actually failed on re-run (verifiable by checking the procedure tracker log)
applies_to:
  - procedure-testing
  - procedure-validation
  - self-healing
  - self-improvement
  - quality-assurance
allowed_tools:
  - code_run
  - vault_read_note
  - vault_safe_write
  - md_safe_replace
  - vault_lint
  - llm_generate
  - run_procedure
summary: PROCESSIONAL_TESTING|PASSRESULT_INTEGRATION|AUTOMATED_FIXER_RETEST_LOOP
tags:
  - procedure
  - procedures
  - meta-procedure
  - testing
  - self-healing
---

# Test-Procedure-Until-Pass

## When to Run This

Run this to **test a procedure and auto-heal it if it fails**. It runs the
target procedure, checks the pass/fail result, and if it failed, invokes
[[Procedure-Fixer]] to diagnose + patch the root cause, then re-runs the
target. This repeats up to `max_iterations` (default 3). If the target
passes on any iteration, the loop exits early with success.

This is the automated version of what a human reviewer does: try it,
see what breaks, fix it, try again. The whole point is that **procedures
should be tested before they're trusted**, and broken procedures should
be iterated on until they actually deliver what they promise.

## Why This Exists

Procedures were being trusted without being tested, and broken procedures were left broken. This procedure exists to automate the test→diagnose→fix→retest loop, composing run_procedure with Procedure-Fixer into a self-healing cycle. The key tradeoff: it caps retries at max_iterations (default 3) so a fundamentally broken procedure fails loudly rather than looping forever.

## Inputs

- `procedure_name` (required): The wikilink title of the procedure to test.
- `procedure_args` (optional): A JSON object of arguments to pass to the
  target procedure when running it (e.g. `{"file_path": "..."}`). If the
  target procedure needs inputs, provide them here — otherwise it will
  fail with "argument required" and that's a false negative.
- `max_iterations` (optional, default 3): Max test→fix→retest cycles
  before giving up.

## How It Works

```
iteration 1:
  run target → passed? → DONE (success)
                failed? → extract failure info → Procedure-Fixer → patch
iteration 2:
  run target → passed? → DONE (success)
                failed? → extract failure info → Procedure-Fixer → patch
iteration 3:
  run target → passed? → DONE (success)
                failed? → DONE (failure, report diagnosis)
```

The fix step is delegated to [[Procedure-Fixer]], which composes its own
diagnostic sub-procedures (Analyze-Failure-Log, Check-Tool-Coverage,
Procedure-Coverage-Check, Check-Procedure-Drift) to find the root cause
and apply a patch via safe writes. This procedure only adds the glue:
the test→fix→retest loop.

## Steps

### Step 1: Run the target procedure and capture the result

1. ```python
import json

# Inputs from the caller
target_name = args.get("procedure_name", "")
proc_args = args.get("procedure_args", {})
max_iter = int(args.get("max_iterations", 3))

if not target_name:
    result = json.dumps({"error": "procedure_name argument required"})
else:
    # Run the target procedure with its args
    run_result = run_procedure(target_name, proc_args)
    # run_procedure returns a dict (or JSON string) with:
    #   overall_passed, failed_step, steps_executed, final_output, step_details
    if isinstance(run_result, str):
        try:
            run_data = json.loads(run_result)
        except json.JSONDecodeError:
            run_data = {"overall_passed": False,
                        "final_output": run_result[:2000],
                        "error": "unparseable result"}
    else:
        run_data = run_result

    result = json.dumps({
        "target": target_name,
        "iteration": 1,
        "max_iterations": max_iter,
        "overall_passed": run_data.get("overall_passed", False),
        "failed_step": run_data.get("failed_step"),
        "steps_executed": run_data.get("steps_executed", 0),
        "final_output": str(run_data.get("final_output", ""))[:3000],
        "step_details": run_data.get("step_details", []),
        "proc_args": proc_args,
        "status": "passed" if run_data.get("overall_passed") else "failed"
    })
    ```

### Step 2: If failed, diagnose the failure with the LLM to extract a fixable root cause

2. ```python
import json as _json

data = _json.loads(output)
if data.get("overall_passed"):
    # Target passed on first try — nothing to fix
    result = output  # pass through to step 6 (success report)
else:
    failure_info = {
        "procedure": data.get("target"),
        "failed_step": data.get("failed_step"),
        "steps_executed": data.get("steps_executed"),
        "final_output": data.get("final_output", "")[:2000],
        "step_details": data.get("step_details", [])[:5],
    }
    prompt = f"""A procedure failed during testing. Diagnose the MOST LIKELY root cause from this failure data, so Procedure-Fixer knows what to target.

Failure data:
{json.dumps(failure_info, indent=2)}

Common root causes:
1. Missing allowed_tools — a code step uses a tool not in frontmatter allowed_tools
2. Missing argument — code step reads args.get("X") but X wasn't passed
3. Broken LLM step — an [llm: ...] step has no instruction or malformed template
4. Import error — code step imports a module that doesn't exist
5. Path resolution — file_path can't be resolved to a real file
6. Bare-name error — code step calls a function that isn't injected or imported
7. Syntax error — code step has invalid Python

Return JSON: {{"root_cause": "one-line description", "category": "one of the 7 above", "failed_step": N, "suggested_fix": "what to change"}}
Return ONLY the JSON."""
    diagnosis = llm_generate(prompt)
    result = json.dumps({
        "target": data.get("target"),
        "iteration": data.get("iteration"),
        "max_iterations": data.get("max_iterations"),
        "overall_passed": False,
        "failure_diagnosis": diagnosis,
        "proc_args": data.get("proc_args", {}),
        "status": "diagnosing"
    })
    ```

### Step 3: Apply the fix via Procedure-Fixer (or a targeted md_safe_replace if the fix is simple)

3. ```python
import json as _json

data = _json.loads(output)
if data.get("overall_passed"):
    result = output  # pass through
else:
    diagnosis_str = data.get("failure_diagnosis", "")
    target = data.get("target")
    # Try to parse the diagnosis to see if it's a simple fix we can apply
    # directly (faster than the full Procedure-Fixer pipeline)
    simple_fix_applied = None
    try:
        start = diagnosis_str.find("{")
        end = diagnosis_str.rfind("}")
        diag = _json.loads(diagnosis_str[start:end+1]) if start != -1 else {}
    except _json.JSONDecodeError:
        diag = {}

    category = diag.get("category", "")
    suggested = diag.get("suggested_fix", "")

    # If the fix is "add a tool to allowed_tools", we can do that directly
    # with md_safe_replace — no need for the full Procedure-Fixer pipeline.
    if category == "Missing allowed_tools" and suggested:
        # The suggested_fix should say what tool to add.
        # Read the procedure note, find the allowed_tools block, add the tool.
        # For safety, delegate to Procedure-Fixer which handles this properly.
        fix_result = run_procedure("Procedure-Fixer", {"procedure_name": target})
        simple_fix_applied = "Procedure-Fixer"
    elif category == "Missing argument":
        # The target needs an arg that wasn't passed — this is a CALLER bug,
        # not a procedure bug. The procedure itself is fine; the test was
        # called wrong. Report this and stop.
        result = json.dumps({
            "target": target,
            "iteration": data.get("iteration"),
            "overall_passed": False,
            "status": "caller_error",
            "message": f"The target procedure needs an argument that wasn't provided: {suggested}. Re-run with the correct proc_args.",
            "diagnosis": diag
        })
    else:
        # Full diagnosis via Procedure-Fixer
        fix_result = run_procedure("Procedure-Fixer", {"procedure_name": target})
        simple_fix_applied = "Procedure-Fixer"

        if isinstance(fix_result, str):
            try:
                fix_data = _json.loads(fix_result)
            except _json.JSONDecodeError:
                fix_data = {"final_output": fix_result[:2000]}
        else:
            fix_data = fix_result

        result = json.dumps({
            "target": target,
            "iteration": data.get("iteration"),
            "max_iterations": data.get("max_iterations"),
            "overall_passed": False,
            "fix_method": simple_fix_applied,
            "fix_result": fix_data.get("final_output", str(fix_data)[:2000]),
            "proc_args": data.get("proc_args", {}),
            "diagnosis": diag,
            "status": "fixed" if fix_data.get("overall_passed", False) else "fix_attempted"
        })
    ```

### Step 4: Re-run the target procedure to check if the fix worked

4. ```python
import json as _json

data = _json.loads(output)
if data.get("overall_passed") or data.get("status") == "caller_error":
    result = output  # pass through
else:
    target = data.get("target")
    proc_args = data.get("proc_args", {})
    iteration = int(data.get("iteration", 1))

    # Re-run the target after the fix
    run_result = run_procedure(target, proc_args)
    if isinstance(run_result, str):
        try:
            run_data = _json.loads(run_result)
        except _json.JSONDecodeError:
            run_data = {"overall_passed": False, "final_output": run_result[:2000]}
    else:
        run_data = run_result

    passed_now = run_data.get("overall_passed", False)

    result = json.dumps({
        "target": target,
        "iteration": iteration,
        "max_iterations": data.get("max_iterations", 3),
        "overall_passed": passed_now,
        "previous_fix": data.get("fix_method"),
        "failed_step": run_data.get("failed_step"),
        "steps_executed": run_data.get("steps_executed", 0),
        "final_output": str(run_data.get("final_output", ""))[:3000],
        "proc_args": proc_args,
        "status": "passed" if passed_now else "still_failing"
    })
    ```

### Step 5: Loop back to step 2 if still failing and under the iteration cap, else finish

5. ```python
import json as _json

data = _json.loads(output)

if data.get("overall_passed") or data.get("status") == "caller_error":
    result = output  # done — pass to final report
else:
    iteration = int(data.get("iteration", 1))
    max_iter = int(data.get("max_iterations", 3))

    if iteration >= max_iter:
        # Hit the cap — report failure
        result = json.dumps({
            "target": data.get("target"),
            "iterations_tried": iteration,
            "max_iterations": max_iter,
            "overall_passed": False,
            "final_output": data.get("final_output", "")[:2000],
            "status": "failed_max_iterations",
            "message": f"Procedure {data.get('target')} still failing after {iteration} test-fix-retest cycles. Needs manual review."
        })
    else:
        # Still failing, under cap — loop back: increment iteration, re-diagnose
        # Re-structure the data for step 2 (diagnose the NEW failure)
        result = json.dumps({
            "target": data.get("target"),
            "iteration": iteration + 1,
            "max_iterations": max_iter,
            "overall_passed": False,
            "failed_step": data.get("failed_step"),
            "steps_executed": data.get("steps_executed", 0),
            "final_output": data.get("final_output", "")[:2000],
            "step_details": data.get("step_details", [])[:5],
            "proc_args": data.get("proc_args", {}),
            "status": "failed"
        })
    ```

### Step 6: Final report

6. ```python
import json as _json

data = _json.loads(output)
passed = data.get("overall_passed", False)
target = data.get("target", "?")
iterations = data.get("iterations_tried", data.get("iteration", 1))

if passed:
    report = f"""## ✅ Procedure Test: PASSED

**Procedure:** {target}
**Iterations:** {iterations}
**Result:** The procedure ran successfully and delivered what it promises.

No fixes were needed (or fixes were applied and the re-run passed).
"""
else:
    report = f"""## ❌ Procedure Test: FAILED

**Procedure:** {target}
**Iterations:** {iterations}
**Status:** {data.get("status", "failed")}
**Last failure:** {data.get("final_output", "")[:500]}

The procedure could not be auto-healed within the iteration cap.
**Manual review needed.** The diagnosis from the last iteration is above.
"""

result = json.dumps({
    "procedure": target,
    "passed": passed,
    "iterations": iterations,
    "report": report,
    "raw": data
})
    ```

## Composition Map

| Step | Delegates to | Purpose |
|------|-------------|---------|
| 1 | `run_procedure(target)` | Run the target procedure, capture pass/fail |
| 2 | (small LLM call) | Diagnose the root cause from failure data |
| 3 | [[Procedure-Fixer]] | Apply a patch to the broken procedure |
| 4 | `run_procedure(target)` | Re-run the target to verify the fix |
| 5 | (Python control flow) | Loop or stop based on pass/fail + iteration cap |
| 6 | (Python) | Final report |

This procedure adds the glue (the test→fix→retest loop) and delegates
the actual fixing to [[Procedure-Fixer]], which itself composes four
diagnostic sub-procedures. The whole chain is:

```
Test-Procedure-Until-Pass
  └─ run_procedure(target)
  └─ Procedure-Fixer
       ├─ Analyze-Failure-Log
       ├─ Check-Tool-Coverage
       ├─ Procedure-Coverage-Check
       ├─ Check-Procedure-Drift
       └─ Procedure-Eval (post-fix verification)
  └─ run_procedure(target)  ← re-test
```

## Falsifiability

This procedure is falsifiable if:
- It reports "passed" but the target procedure produces wrong output on
  re-run (checkable by reading the procedure tracker log)
- It reports "failed" but the target actually passes when run manually
  (checkable by running `execute_procedure(target)` directly)
- It loops forever (prevented by the `max_iterations` cap, default 3)

## Notes

- **Caller errors vs procedure bugs:** If the diagnosis is "Missing
  argument", the procedure itself is fine — the *caller* didn't pass the
  right args. This procedure detects that and reports it as a
  `caller_error` instead of trying to fix a non-bug.
- **Idempotency:** Each iteration runs the target fresh, so fixes from
  the previous iteration are picked up automatically.
- **Safety:** All fixes go through [[Procedure-Fixer]], which uses safe
  writes with backups. The original procedure note is never destroyed.

## Related

- [[Procedure-Fixer]] — the diagnose+fix procedure this loop delegates to
- [[Test-RunProcedure-Return]] — inspects what run_procedure returns
- [[Verify-Procedure-Args]] — checks a procedure's code steps for runtime issues