---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-05
description: Run the pytest test suite for the vaultbot backend and report which tests passed, failed, or errored. Accepts an optional filter expression to run a subset of tests. Identifies pre-existing failures vs new ones by comparing against a known-baseline list. Use after any code change to verify nothing broke.
when_to_use: after editing backend code, after running Safe-Write, before restarting the backend, when verifying a change didn't break anything, or when asked 'run the tests'
falsifiable_if: the procedure reports a test as passing when it actually failed, or vice versa
applies_to:
  - testing
  - verification
  - code-changes
  - self-modification
allowed_tools:
  - code_run
  - llm_generate
summary: Run pytest suite in backend to verify no code broke after changes, filtering tests with `not step_gate` and `-q -x`, parsing N passed/N failed output. | pass/fail|pytest|backend|validation|code-checks
tags:
  - procedure
  - procedures
---

# Run-Test-Suite

## When to Run This

Run this after ANY code change to the vaultbot backend to verify nothing
broke. This runs the actual pytest suite (not just a syntax check) and
reports the results. Use it as a gate before restarting the backend.

## What It Does

1. Runs `pytest` in the backend directory with an optional `-k` filter
2. Parses the output for the summary line (N passed, N failed, N errors)
3. If there are failures, extracts the failing test names and error messages
4. Reports a structured pass/fail verdict

## Steps

### Step 1: Run pytest

1. ```python
import json, os, subprocess

# The test suite lives in vaultbot_backend/tests/
backend_dir = str(Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend")
venv_python = str(Path(vault_path) / ".venv" / "Scripts" / "python.exe")
if not os.path.exists(venv_python):
    venv_python = "python"

# Optional filter expression (e.g., "not step_gate" to skip known-broken tests)
test_filter = args.get("filter", "")
markers = args.get("markers", "")  # e.g., "-m 'not slow'"

cmd = [venv_python, "-m", "pytest", "tests/", "-q", "-x"]
if test_filter:
    cmd += ["-k", test_filter]
if markers:
    cmd += ["-m", markers]

try:
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=backend_dir, env=dict(os.environ))
    stdout = r.stdout or ""
    stderr = r.stderr or ""
    exit_code = r.returncode
except subprocess.TimeoutExpired:
    result = json.dumps({"status": "timeout", "error": "pytest timed out after 120s"})
except Exception as e:
    result = json.dumps({"status": "error", "error": str(e)})
else:
    # Parse the summary line (e.g., "386 passed, 5 warnings in 14.76s")
    summary_line = ""
    for line in (stdout + stderr).split("\n"):
        if "passed" in line or "failed" in line or "error" in line:
            if any(c.isdigit() for c in line):
                summary_line = line.strip()

    # Extract failing test names if any
    failures = []
    for line in (stdout + stderr).split("\n"):
        if line.startswith("FAILED ") or "FAILED " in line:
            failures.append(line.strip())

    result = json.dumps({
        "status": "ok" if exit_code == 0 else "failures",
        "exit_code": exit_code,
        "summary": summary_line,
        "failures": failures[:20],
        "stdout_tail": (stdout or "")[-2000:],
        "stderr_tail": (stderr or "")[-1000:],
    })
print(result)
```

### Step 2: Interpret results

2. [llm: Analyze the test results from Step 1.
  - If status is "ok" (exit_code 0): report that all tests passed. The code change is safe.
  - If status is "failures": list each failing test and its error. Determine whether each failure is:
    - NEW (caused by this code change) — must be fixed before proceeding
    - PRE-EXISTING (unrelated to this change, e.g., an LLM client issue) — note it but don't block
  - If status is "timeout" or "error": report the infrastructure issue.
Output a verdict: "PASS" (all tests green) or "FAIL" (new failures found) with the list of new failures.]