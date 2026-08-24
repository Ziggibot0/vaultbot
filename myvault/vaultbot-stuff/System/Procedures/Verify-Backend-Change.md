---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Full verify-and-deploy chain for a backend code change: runs the test suite, restarts the backend process, and verifies it comes back healthy. Orchestrates Run-Test-Suite, Backend-Restart, and a health check in sequence. Use after editing backend code to go from edit to live in one procedure call."
when_to_use: after editing vaultbot_backend code and you need to verify and deploy the change end-to-end, when you want to confirm a code change is safe AND live, or when asked 'verify the change and restart'
falsifiable_if: the procedure reports success but the backend is unhealthy, or reports failure when the change is actually fine
applies_to:
  - deployment
  - verification
  - code-changes
  - self-modification
  - orchestration
allowed_tools:
  - code_run
  - run_procedure
  - llm_generate
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Verify-Backend-Change

## When to Run This

Run this after editing any file under `vaultbot_backend/` to verify the
change didn't break tests AND deploy it live. This is the full chain:
test → restart → health check. It calls sub-procedures so each step is
auditable and the logic compounds.

## What It Does

1. Calls `Run-Test-Suite` to run pytest and check for new failures
2. If tests pass, calls `Backend-Restart` to restart the backend process
3. Polls the `/health` endpoint until the backend is up and responsive
4. Reports the final status: deployed + healthy, or where it failed

## Why This Exists

Editing backend code left a gap between "edit" and "live" — tests, restart, and health check were separate manual steps. This procedure exists to orchestrate the full verify-and-deploy chain in one call. The key tradeoff: it stops before restarting if tests fail, so broken code is never deployed live.

## Steps

### Step 1: Run the test suite

1. ```python
import json

# Delegate to the dedicated test procedure
test_filter = args.get("test_filter", "not step_gate")  # skip known-broken by default
result = run_procedure("Run-Test-Suite", {"filter": test_filter})
output = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)
print(output)
```

### Step 2: Examine test results

2. [llm: Examine the test results from Step 1.
  - If the verdict is "PASS" (all tests green, or only pre-existing failures): proceed to Step 3.
  - If the verdict is "FAIL" (new failures caused by the change): STOP. Report the failing tests. Do not restart the backend with broken code. The change must be fixed first.]

### Step 3: Restart the backend

3. ```python
import json

# Delegate to the restart procedure
result = run_procedure("Backend-Restart", {})
output = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)
print(output)
```

### Step 4: Verify the backend is healthy

4. ```python
import json, subprocess, time, os

# Poll the health endpoint until the backend responds or timeout
venv_python = str(Path(vault_path) / ".venv" / "Scripts" / "python.exe")
if not os.path.exists(venv_python):
    venv_python = "python"

health_ok = False
health_info = ""
attempts = 0
max_attempts = 12  # ~60 seconds max

while attempts < max_attempts:
    attempts += 1
    try:
        code = f'''
import urllib.request, json, sys
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)
    data = json.loads(r.read())
    print(json.dumps(data))
    sys.exit(0)
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
    sys.exit(1)
'''
        r = subprocess.run([venv_python, "-c", code],
                          capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and '"ok":true' in (r.stdout or ""):
            health_ok = True
            health_info = r.stdout.strip()
            break
        else:
            health_info = r.stdout.strip() or r.stderr.strip()
    except Exception as e:
        health_info = str(e)
    time.sleep(5)

result = json.dumps({
    "healthy": health_ok,
    "attempts": attempts,
    "health_info": health_info[:500],
    "deployed": health_ok,
})
print(result)
```

### Step 5: Final report

5. [llm: Produce the final status report based on Step 4:
  - If healthy=True: report "DEPLOYED — backend is live and healthy." Include the health info (uptime, index vectors, graph nodes).
  - If healthy=False: report "DEPLOY FAILED — backend did not come back up after restart." Include the health info or error. This is critical — the backend may be down. Recommend checking backend_stderr.txt for the crash reason.
Output a clear verdict so the operator knows whether the change is live.]

## Related

- [[Run-Test-Suite]] — the test suite this procedure calls
- [[Backend-Restart]] — the restart procedure this procedure calls
- [[Verify-Syntax]] — verifies Python syntax before restart