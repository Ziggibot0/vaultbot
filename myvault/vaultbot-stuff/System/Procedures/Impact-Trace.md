---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Trace the blast radius of changing a function or variable in the backend. Given a function name, finds every file that imports it, every file that calls it, and every procedure that references it. Returns a dependency map so you know what to test before making the change.
when_to_use: before editing a shared function, before renaming a variable or function, when assessing the risk of a code change, or when asked 'what will break if I change X'
falsifiable_if: the trace misses a file that imports or calls the target, or includes files that don't actually reference it
applies_to:
  - self-modification
  - impact-analysis
  - dependency-tracing
  - risk-assessment
allowed_tools:
  - code_read
  - vault_list
  - llm_generate
summary: Impact-Trace
tags:
  - procedure
  - procedures
---

# Impact-Trace

## When to Run This

Before changing a function or variable that other code might depend on,
run this to see the blast radius. It scans the backend and procedures for
every reference.

## Why This Exists

Changing a shared function or variable can break code you didn't know
depended on it. This procedure traces every importer, caller, and procedure
reference to produce a dependency map. The tradeoff: it uses regex matching,
so it may miss dynamic references or include false positives.

## Steps

### Step 1: Scan all backend .py files for references to the target

1. ```python
import re, json

target = args.get("function_name", args.get("target", ""))
if not target:
    result = json.dumps({"error": "function_name or target argument required"})
else:
    backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
    importers = []
    callers = []
    for py in backend_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(py.relative_to(backend_dir))
        # Check imports
        if re.search(rf'(?:from\s+{re.escape(target)}|import\s+{re.escape(target)})', text):
            importers.append(rel)
        # Check calls
        if re.search(rf'\b{re.escape(target)}\s*\(', text):
            lines = text.split('\n')
            for i, line in enumerate(lines, 1):
                if re.search(rf'\b{re.escape(target)}\s*\(', line):
                    callers.append({"file": rel, "line": i, "code": line.strip()[:100]})

    # Check procedures too
    proc_dir = Path(vault_path) / "vaultbot-stuff" / "System" / "Procedures"
    proc_refs = []
    for p in proc_dir.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if re.search(rf'\b{re.escape(target)}\b', text):
            proc_refs.append(p.stem)

    result = json.dumps({
        "target": target,
        "importers": list(set(importers)),
        "callers": callers[:20],
        "procedure_references": list(set(proc_refs)),
        "total_call_sites": len(callers),
    })
```

### Step 2: Small model assesses risk

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Assess the risk of changing this function/variable:
Target: {data['target']}
Imported by: {data['importers']}
Call sites: {data['total_call_sites']}
Procedures referencing it: {data['procedure_references']}

Return JSON: {{"risk": "low|medium|high", "affected_count": N, "recommendation": "safe to change|test X first|don't change without coordination", "test_files": ["files to verify after change"]}}
Return ONLY the JSON."""
    assessment = llm_generate(prompt)
    result = assessment
```

### Step 3: Return the impact report

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"risk": "unknown"}
result = _json.dumps({"impact": data, "risk_assessment": parsed})
```

## Related

- [[Git-Working-Diff]] — inspect uncommitted changes before committing
- [[Verify-Backend-Change]] — verify a backend change after applying it
- [[Code-Structure-Check]] — structural analysis of backend code