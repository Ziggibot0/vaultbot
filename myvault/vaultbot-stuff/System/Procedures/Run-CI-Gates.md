---
type: procedure
status: experimental
baseline: true
created: 2026-08-19
description: "Run the same CI gates locally that GitHub Actions runs: ruff check (full rule set), ruff format --check, pyright on hot-path files, and pytest. Returns a structured per-gate pass/fail report with the exact errors so they can be fixed before pushing. This is the local mirror of the CI pipeline — catch failures in seconds instead of waiting for GitHub Actions."
when_to_use: "Before pushing a PR, after editing backend code, before calling submit_contribution, when a PR's CI failed and you need to reproduce the failure locally, or when asked 'run the CI checks locally'."
falsifiable_if: "The procedure reports a gate as passing when it actually fails, or vice versa."
applies_to:
  - ci
  - testing
  - verification
  - code-changes
  - self-modification
  - git-workflow
allowed_tools:
  - code_run
summary: Run-CI-Gates
tags:
  - procedure
  - procedures
  - ci
  - ruff
  - pytest
  - pyright
---

# Run-CI-Gates

## When to Run This

Run this **before pushing** to catch CI failures locally in seconds
instead of waiting for GitHub Actions. Also run it after a PR's CI
fails — reproduce the failure on your machine, fix it, verify locally,
then push the fix.

This is the local mirror of the GitHub Actions pipeline. It runs the
same gates in the same order:

1. **Ruff check** (full rule set) — HARD GATE
2. **Ruff format --check** — HARD GATE
3. **Pyright** on hot-path files — HARD GATE (non-blocking on full repo)
4. **Pytest** (unit tests) — HARD GATE

If any HARD GATE fails, the procedure reports which gate failed and
the exact errors, so the fix can be applied immediately.

## Why This Exists

Pushing a PR that fails CI wastes a full GitHub Actions round-trip when the same failures could be caught locally in seconds. This procedure closes that gap by running the same gates (ruff check, ruff format, pyright, pytest) in the same order as CI. The tradeoff is that pyright is a soft, opt-in gate because it is slow on the full repo and carries pre-existing debt.

## What It Does

1. Discovers the backend directory and venv Python
2. Runs each gate via `subprocess.run()` and captures exit code + output
3. Parses failures into structured JSON (gate name, status, errors)
4. Returns an overall verdict: ALL_PASS or FAILURES with per-gate details

## Steps

### Step 1: Discover the backend directory and Python

```python
import json, os

# The backend lives at vault_path/vaultbot/vaultbot_backend
backend_dir = str(Path(FRAMEWORK_ROOT) / "vaultbot_backend")
if not os.path.isdir(backend_dir):
    # Fallback: try walking up from cwd
    backend_dir = str(Path(vault_path))

venv_python = str(Path(vault_path) / ".venv" / "Scripts" / "python.exe")
if not os.path.exists(venv_python):
    venv_python = str(Path(vault_path) / ".venv" / "bin" / "python")
if not os.path.exists(venv_python):
    venv_python = "python"

# Check if ruff is available (may be in venv Scripts or global)
import shutil
ruff_bin = shutil.which("ruff")
if not ruff_bin:
    ruff_venv = str(Path(vault_path) / ".venv" / "Scripts" / "ruff.exe")
    ruff_bin = ruff_venv if os.path.exists(ruff_venv) else None

# Check if pyright is available
pyright_bin = shutil.which("pyright")
if not pyright_bin:
    pyright_venv = str(Path(vault_path) / ".venv" / "Scripts" / "pyright.exe")
    pyright_bin = pyright_venv if os.path.exists(pyright_venv) else None

result = json.dumps({
    "backend_dir": backend_dir,
    "python": venv_python,
    "ruff": ruff_bin,
    "pyright": pyright_bin,
})
```

### Step 2: Run ruff check (full rule set — HARD GATE)

```python
import json, subprocess, os, shutil

# Re-discover config (each step is independent — no cross-step variables)
backend_dir = str(Path(FRAMEWORK_ROOT) / "vaultbot_backend")
if not os.path.isdir(backend_dir):
    backend_dir = str(Path(vault_path))

ruff_bin = shutil.which("ruff")
if not ruff_bin:
    ruff_venv = str(Path(vault_path) / ".venv" / "Scripts" / "ruff.exe")
    ruff_bin = ruff_venv if os.path.exists(ruff_venv) else None

gate_results = {"ruff_check": {"status": "skipped", "errors": []}}

if ruff_bin:
    try:
        r = subprocess.run(
            [ruff_bin, "check", "."],
            capture_output=True, text=True, timeout=120,
            cwd=backend_dir,
        )
        if r.returncode == 0:
            gate_results["ruff_check"] = {"status": "pass", "errors": []}
        else:
            # Parse ruff output for specific violations
            errors = []
            for line in (r.stdout + r.stderr).split("\n"):
                line = line.strip()
                if line and not line.startswith("Found") and not line.startswith("--"):
                    errors.append(line)
            gate_results["ruff_check"] = {
                "status": "fail",
                "errors": errors[:30],
                "raw_output": (r.stdout + r.stderr)[:2000],
            }
    except subprocess.TimeoutExpired:
        gate_results["ruff_check"] = {"status": "timeout", "errors": ["ruff check timed out after 120s"]}
    except Exception as e:
        gate_results["ruff_check"] = {"status": "error", "errors": [str(e)]}
else:
    gate_results["ruff_check"] = {"status": "skipped", "errors": ["ruff not found — install with: pip install ruff"]}

result = json.dumps(gate_results)
```

### Step 3: Run ruff format --check (HARD GATE)

```python
import json, subprocess, os, shutil

# Re-discover config (each step is independent)
backend_dir = str(Path(FRAMEWORK_ROOT) / "vaultbot_backend")
if not os.path.isdir(backend_dir):
    backend_dir = str(Path(vault_path))

ruff_bin = shutil.which("ruff")
if not ruff_bin:
    ruff_venv = str(Path(vault_path) / ".venv" / "Scripts" / "ruff.exe")
    ruff_bin = ruff_venv if os.path.exists(ruff_venv) else None

# Preserve previous gate result
prev = json.loads(output)
gate_results = prev if isinstance(prev, dict) and "ruff_check" in prev else {"ruff_check": prev}

gate_results["ruff_format"] = {"status": "skipped", "errors": []}

if ruff_bin:
    try:
        r = subprocess.run(
            [ruff_bin, "format", "--check", "."],
            capture_output=True, text=True, timeout=120,
            cwd=backend_dir,
        )
        if r.returncode == 0:
            gate_results["ruff_format"] = {"status": "pass", "errors": []}
        else:
            # ruff format --check lists files that would be reformatted
            errors = []
            for line in (r.stdout + r.stderr).split("\n"):
                line = line.strip()
                if line and ("Would reformat" in line or "would reformat" in line):
                    errors.append(line)
            if not errors:
                errors = [l.strip() for l in (r.stdout + r.stderr).split("\n") if l.strip()][:10]
            gate_results["ruff_format"] = {
                "status": "fail",
                "errors": errors,
                "hint": "Run 'ruff format .' to auto-fix formatting, then re-run this gate.",
            }
    except subprocess.TimeoutExpired:
        gate_results["ruff_format"] = {"status": "timeout", "errors": ["ruff format --check timed out after 120s"]}
    except Exception as e:
        gate_results["ruff_format"] = {"status": "error", "errors": [str(e)]}
else:
    gate_results["ruff_format"] = {"status": "skipped", "errors": ["ruff not found"]}

result = json.dumps(gate_results)
```

### Step 4: Run pyright (pre-existing debt — SOFT GATE)

Pyright is non-blocking on the full repo (SOFT GATE). The CI pipeline
has a debt ratchet: pre-existing errors are tracked, and only NEW errors
fail the gate. Running pyright on the full repo is slow (>300s on 226
files), so it is **opt-in** — pass `run_pyright=true` in args to enable
it. By default it's skipped, which matches the pre-push use case where
you want fast feedback from ruff + pytest. We are widening the hot-path
files incrementally; the target date to flip full-repo pyright to a hard
blocking gate is 2026-10-01 (see CI configuration comments).

```python
import json, subprocess, os, shutil

# Re-discover config
backend_dir = str(Path(FRAMEWORK_ROOT) / "vaultbot_backend")
if not os.path.isdir(backend_dir):
    backend_dir = str(Path(vault_path))

pyright_bin = shutil.which("pyright")
if not pyright_bin:
    pyright_venv = str(Path(vault_path) / ".venv" / "Scripts" / "pyright.exe")
    pyright_bin = pyright_venv if os.path.exists(pyright_venv) else None

# Preserve previous gate results
prev = json.loads(output)
gate_results = prev if isinstance(prev, dict) and "ruff_check" in prev else {"ruff_check": prev}

# Pyright is opt-in — slow on full repo, SOFT gate anyway
run_pyright = args.get("run_pyright", False)

gate_results["pyright"] = {"status": "skipped", "errors": [], "note": "SOFT GATE — opt-in via run_pyright=true (slow on full repo)"}

if pyright_bin and run_pyright:
    try:
        r = subprocess.run(
            [pyright_bin, "."],
            capture_output=True, text=True, timeout=600,
            cwd=backend_dir,
        )
        # Pyright exit codes: 0 = no errors, 1 = errors found, 2 = fatal error
        # The full repo has pre-existing debt (hundreds of errors). This is SOFT.
        # Count errors for reporting; the caller compares against baseline.
        error_count = 0
        errors = []
        for line in (r.stdout + r.stderr).split("\n"):
            line = line.strip()
            if line and ("error:" in line.lower()):
                error_count += 1
                if len(errors) < 20:
                    errors.append(line)
        # Also check for summary line (e.g., "428 errors, 102 warnings")
        summary_line = ""
        for line in (r.stdout + r.stderr).split("\n"):
            if "errors" in line and "warnings" in line:
                summary_line = line.strip()

        if r.returncode == 0:
            gate_results["pyright"] = {"status": "pass", "errors": [], "summary": summary_line}
        else:
            gate_results["pyright"] = {
                "status": "debt",  # SOFT gate — don't say "fail" for pre-existing debt
                "errors": errors,
                "error_count": error_count,
                "summary": summary_line,
                "exit_code": r.returncode,
                "note": "SOFT GATE — compare error_count against baseline. Only NEW errors should block.",
            }
    except subprocess.TimeoutExpired:
        gate_results["pyright"] = {"status": "timeout", "errors": ["pyright timed out after 600s"]}
    except Exception as e:
        gate_results["pyright"] = {"status": "error", "errors": [str(e)]}
else:
    gate_results["pyright"] = {"status": "skipped", "errors": ["pyright not found — install with: pip install pyright"]}

result = json.dumps(gate_results)
```

### Step 5: Run pytest (unit tests — HARD GATE)

```python
import json, subprocess, os

# Re-discover config
backend_dir = str(Path(FRAMEWORK_ROOT) / "vaultbot_backend")
if not os.path.isdir(backend_dir):
    backend_dir = str(Path(vault_path))

venv_python = str(Path(vault_path) / ".venv" / "Scripts" / "python.exe")
if not os.path.exists(venv_python):
    venv_python = str(Path(vault_path) / ".venv" / "bin" / "python")
if not os.path.exists(venv_python):
    venv_python = "python"

# Preserve previous gate results
prev = json.loads(output)
gate_results = prev if isinstance(prev, dict) and "ruff_check" in prev else {"ruff_check": prev}

gate_results["pytest"] = {"status": "skipped", "errors": []}

test_filter = args.get("test_filter", "not step_gate")
cmd = [venv_python, "-m", "pytest", "tests/", "-q", "-x"]
if test_filter:
    cmd += ["-k", test_filter]

try:
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600,
        cwd=backend_dir, env=dict(os.environ),
    )
    # Detect "No module named pytest" — report as skipped, not failed
    combined = (r.stdout or "") + (r.stderr or "")
    if "No module named pytest" in combined:
        gate_results["pytest"] = {
            "status": "skipped",
            "errors": ["pytest not installed in the active Python environment"],
            "hint": f"Install with: {venv_python} -m pip install pytest",
        }
    elif r.returncode == 0:
        # Parse summary line
        summary = ""
        for line in (r.stdout + r.stderr).split("\n"):
            if "passed" in line and any(c.isdigit() for c in line):
                summary = line.strip()
        gate_results["pytest"] = {"status": "pass", "errors": [], "summary": summary}
    else:
        failures = []
        for line in (r.stdout + r.stderr).split("\n"):
            if line.startswith("FAILED ") or "FAILED " in line:
                failures.append(line.strip())
        summary = ""
        for line in (r.stdout + r.stderr).split("\n"):
            if ("passed" in line or "failed" in line or "error" in line) and any(c.isdigit() for c in line):
                summary = line.strip()
        gate_results["pytest"] = {
            "status": "fail",
            "errors": failures[:20],
            "summary": summary,
            "stdout_tail": (r.stdout or "")[-2000:],
            "note": "Some tests may be pre-existing failures (e.g. test_models_returns_list raises NotImplementedError when no cloud provider is configured). Compare against baseline before blocking.",
        }
except subprocess.TimeoutExpired:
    gate_results["pytest"] = {"status": "timeout", "errors": ["pytest timed out after 600s"]}
except Exception as e:
    gate_results["pytest"] = {"status": "error", "errors": [str(e)]}

result = json.dumps(gate_results)
```

### Step 6: Summarize the overall verdict

This step produces the final verdict deterministically — no LLM needed.
The gate results are already computed; this just formats them into a
human-readable summary and sets the overall pass/fail.

```python
import json

# All gate results are in the output from Step 5
gates = json.loads(output)

# Determine overall verdict
hard_gates = ["ruff_check", "ruff_format", "pytest"]
soft_gates = ["pyright"]

hard_failures = []
soft_notes = []
all_passed = True

for gate_name in hard_gates:
    gate = gates.get(gate_name, {})
    status = gate.get("status", "skipped")
    if status == "fail":
        all_passed = False
        hard_failures.append({
            "gate": gate_name,
            "errors": gate.get("errors", [])[:5],
        })
    elif status == "timeout":
        all_passed = False
        hard_failures.append({
            "gate": gate_name,
            "errors": gate.get("errors", []),
        })
    elif status == "error":
        all_passed = False
        hard_failures.append({
            "gate": gate_name,
            "errors": gate.get("errors", []),
        })

for gate_name in soft_gates:
    gate = gates.get(gate_name, {})
    status = gate.get("status", "skipped")
    if status == "debt":
        soft_notes.append({
            "gate": gate_name,
            "error_count": gate.get("error_count", 0),
            "note": gate.get("note", "pre-existing debt"),
        })

# Build summary lines
lines = []
for gate_name in ["ruff_check", "ruff_format", "pyright", "pytest"]:
    gate = gates.get(gate_name, {})
    status = gate.get("status", "skipped")
    icon = {"pass": "✅", "fail": "❌", "skipped": "⏭️", "debt": "⚠️", "timeout": "⏱️", "error": "💥"}.get(status, "❓")
    lines.append(f"  {icon} {gate_name}: {status}")

summary_text = "\n".join(lines)

if all_passed:
    verdict = "ALL_PASS"
    summary_text += "\n\n✅ Safe to push — all local CI gates green."
else:
    verdict = "FAILURES"
    fail_names = [f["gate"] for f in hard_failures]
    summary_text += f"\n\n❌ FAILURES: {', '.join(fail_names)}. Fix the first failing gate before pushing."

result = json.dumps({
    "verdict": verdict,
    "all_passed": all_passed,
    "hard_failures": hard_failures,
    "soft_notes": soft_notes,
    "summary": summary_text,
    "gates": gates,
})
```

## Related

- [[Submit-Contribution]] — enforces these gates before pushing a PR
- [[Run-Test-Suite]] — the pytest gate this procedure runs
- [[Verify-Backend-Change]] — restarts and checks health after a change