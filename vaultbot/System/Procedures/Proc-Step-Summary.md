---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Verify a self-modification edit didn't break imports. Given a file path that was just edited, attempts to import it in a subprocess and reports any import errors. Does NOT restart the backend — just checks if the file is syntactically and import-valid. Use after every Safe-Write edit to catch breakages immediately.
when_to_use: after editing a backend .py file, after running Safe-Write, before restarting the backend, or when verifying an edit didn't break anything
falsifiable_if: the procedure reports a file is importable when it isn't, or reports an import error that doesn't exist
applies_to:
  - self-modification
  - verification
  - import-check
  - post-edit
allowed_tools:
  - code_read
  - llm_generate
summary: Proc-Step-Summary
tags:
  - procedure
  - procedures
---

# Proc-Step-Summary

## When to Run This

After every code edit, run this to verify the file still imports cleanly.
It catches breakages immediately — before you restart the backend and
discover the hard way.

## Why This Exists

A self-modification edit can break imports without throwing a visible error until the backend restarts. This procedure closes that gap by attempting to import the edited file in a subprocess and reporting any import errors immediately. The tradeoff is that it checks import validity only — it does not restart the backend or run the full test suite.

## Steps

### Step 1: Attempt to import the edited file in a subprocess

1. ```python
import subprocess, json, sys

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(vault_path) / "vaultbot" / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        backend_dir = p.parent
        module = p.stem
        # Use the venv python
        venv_py = Path(vault_path) / ".venv" / "Scripts" / "python.exe"
        if not venv_py.exists():
            venv_py = Path(sys.executable)
        r = subprocess.run(
            [str(venv_py), "-c", f"import sys; sys.path.insert(0, '{backend_dir}'); import {module}; print('OK')"],
            capture_output=True, text=True, timeout=15)
        result = json.dumps({
            "file": str(p), "module": module,
            "stdout": r.stdout.strip()[:500],
            "stderr": r.stderr.strip()[:500],
            "exit_code": r.returncode,
            "importable": r.returncode == 0,
        })
```

### Step 2: If import failed, small model diagnoses the error

2. ```python
import json as _json

data = _json.loads(output)
if data.get("importable"):
    result = _json.dumps({"status": "ok", "message": "file imports cleanly"})
else:
    prompt = f"""Diagnose this Python import error:
File: {data['file']}
Stderr: {data['stderr']}

Return JSON: {{"error_type": "syntax|import|name|other", "cause": "one sentence", "fix": "suggested fix"}}
Return ONLY the JSON."""
    diagnosis = llm_generate(prompt)
    result = diagnosis
```

### Step 3: Return the verification result

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"status": "error", "message": "could not parse result"}
result = _json.dumps(parsed)
```

## Related

- [[Safe-Write]] — the edit step this procedure verifies
- [[Proc-Step-Planner]] — plans the edits before they are made
- [[Run-Test-Suite]] — the fuller verification gate after import check