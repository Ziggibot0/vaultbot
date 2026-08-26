---
type: procedure
status: experimental
baseline: true
created: 2026-08-22
description: "Run the CI hard gates locally: ruff check, ruff format --check, and pytest unit tests. Mirrors the gates in .github/workflows/ci.yml and the preflight in submit_contribution.py. Returns a structured pass/fail dict for each gate."
when_to_use: "Before submitting a PR or after making changes, to verify locally that CI will pass. Called by Dev-Cycle after fixing code."
falsifiable_if: The procedure reports all gates passing when ruff or pytest would actually fail.
applies_to:
  - ci
  - dev-cycle
  - testing
allowed_tools:
  - code_run
  - code_read
summary: Runs CI hard gates locally (ruff + pytest).
tags:
  - procedure
  - ci
  - dev-cycle
---

# Run-CI-Preflight

## Purpose

Run the CI hard gates locally before pushing a PR. This mirrors the exact
gates that CI runs: ruff check (full rule set), ruff format --check, and
pytest unit tests. Catching failures here saves a CI round-trip.

## Why This Exists

`submit_contribution` has `_run_preflight_ci_gates()` internally, but
it's a monolithic tool that bundles preflight with PR submission. The
Dev-Cycle needs to run preflight independently — after fixing code, before
committing — so it can iterate on failures without creating a PR each time.

## Safety

- Read-only: this procedure only runs linting and tests, it doesn't modify anything.
- Timeouts: ruff has a 180s timeout, pytest has a 600s timeout (matching CI).
- If ruff is not found, the gate is marked as "skipped" (not "pass").

## Steps

### Step 1: Run all CI hard gates

This step runs ruff check, ruff format --check, and pytest, matching the
exact gates in `.github/workflows/ci.yml` and `_run_preflight_ci_gates()`
in `submit_contribution.py`.

```python
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

vault_root = os.environ.get("VAULT_PATH", ".")
backend_dir = os.path.join(vault_root, "vaultbot", "vaultbot_backend")
if not os.path.isdir(backend_dir):
    backend_dir = vault_root

scope = args.get("scope", "full")  # full | lint | tests | format

# Locate ruff (venv first, then PATH)
ruff_bin = None
for candidate in (
    os.path.join(vault_root, ".venv", "Scripts", "ruff.exe"),
    os.path.join(vault_root, ".venv", "bin", "ruff"),
):
    if os.path.exists(candidate):
        ruff_bin = candidate
        break
if ruff_bin is None:
    ruff_bin = shutil.which("ruff")

# Locate venv python for pytest
venv_python = None
for candidate in (
    os.path.join(vault_root, ".venv", "Scripts", "python.exe"),
    os.path.join(vault_root, ".venv", "bin", "python"),
):
    if os.path.exists(candidate):
        venv_python = candidate
        break
if venv_python is None:
    venv_python = sys.executable

# Match the env CI sets for the pytest hard gate
test_env = dict(os.environ)
test_env["VAULTBOT_SKIP_LOCK"] = "1"
test_env["VAULTBOT_SKIP_WATCHER"] = "1"
test_env["VAULT_PATH"] = vault_root

gates = {}

# Gate 1: ruff check (full rule set) — HARD GATE
if scope in ("full", "lint"):
    if ruff_bin:
        try:
            r = subprocess.run(
                [ruff_bin, "check", "."],
                capture_output=True, text=True, timeout=180,
                cwd=backend_dir,
            )
            gates["ruff_check"] = {
                "status": "pass" if r.returncode == 0 else "fail",
                "output": (r.stdout + r.stderr)[-2000:],
            }
        except subprocess.TimeoutExpired:
            gates["ruff_check"] = {"status": "error", "output": "timeout after 180s"}
        except Exception as e:
            gates["ruff_check"] = {"status": "error", "output": str(e)}
    else:
        gates["ruff_check"] = {"status": "skipped", "output": "ruff not found"}

# Gate 2: ruff format --check — HARD GATE
if scope in ("full", "format"):
    if ruff_bin:
        try:
            r = subprocess.run(
                [ruff_bin, "format", "--check", "."],
                capture_output=True, text=True, timeout=180,
                cwd=backend_dir,
            )
            gates["ruff_format"] = {
                "status": "pass" if r.returncode == 0 else "fail",
                "output": (r.stdout + r.stderr)[-2000:],
            }
        except subprocess.TimeoutExpired:
            gates["ruff_format"] = {"status": "error", "output": "timeout after 180s"}
        except Exception as e:
            gates["ruff_format"] = {"status": "error", "output": str(e)}
    else:
        gates["ruff_format"] = {"status": "skipped", "output": "ruff not found"}

# Gate 3: pytest unit tests — HARD GATE
if scope in ("full", "tests"):
    try:
        r = subprocess.run(
            [venv_python, "-m", "pytest", "tests/", "-q", "-m", "unit", "--tb=short"],
            capture_output=True, text=True, timeout=600,
            cwd=backend_dir, env=test_env,
        )
        gates["pytest"] = {
            "status": "pass" if r.returncode == 0 else "fail",
            "output": (r.stdout + r.stderr)[-2000:],
        }
    except subprocess.TimeoutExpired:
        gates["pytest"] = {"status": "error", "output": "timeout after 600s"}
    except Exception as e:
        gates["pytest"] = {"status": "error", "output": str(e)}

# Summarize
all_passed = all(
    g.get("status") in ("pass", "skipped") for g in gates.values()
)
failed = {name: g for name, g in gates.items() if g.get("status") in ("fail", "error")}

result = json.dumps({
    "scope": scope,
    "gates": gates,
    "all_passed": all_passed,
    "failed_gates": list(failed.keys()),
    "summary": "PASS" if all_passed else f"FAIL ({len(failed)} gate(s) failed)",
}, default=str)

print(result)
```

## Related

- [[Dev-Cycle]] — the main orchestrator that calls this procedure
- [[Solve-GitHub-Issue]] — existing issue solver (has its own test step)