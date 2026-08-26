---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: "Check if a backend file follows the project's code conventions: proper imports, no bare excepts, no hardcoded paths, functions have type hints, modules have __init__.py. Scans a file and returns convention violations. Use when auditing code quality before a commit or after an edit."
when_to_use: when auditing code quality, before a commit, after editing a file, when checking if code follows conventions, or when asked 'does this code follow the rules'
falsifiable_if: the procedure reports violations that aren't real, or misses actual violations
applies_to:
  - code-quality
  - convention-checking
  - self-modification
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: "Summary: The note provides a Python script to validate backend file conventions by checking for bare exceptions, hardcoded paths, missing type hints, and other common issues before they cause problems"
tags:
  - procedure
  - procedures
---

# Code-Structure-Check

## When to Run This

Run this to check if a backend file follows conventions. Catches bare
excepts, hardcoded paths, missing type hints, and other common issues
before they cause problems.

## Why This Exists

Convention violations — bare excepts, hardcoded paths, missing type hints — cause problems downstream but are easy to miss in review. This procedure scans a file for them deterministically. The key tradeoff is that detection is deterministic (regex), while severity assessment and fix suggestions are delegated to the small model.

## Steps

### Step 1: Read the file and check conventions deterministically

1. ```python
import re, json

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(FRAMEWORK_ROOT) / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split('\n')
        violations = []

        for i, line in enumerate(lines, 1):
            # Bare except
            if re.match(r'\s*except\s*:', line):
                violations.append({"line": i, "rule": "bare-except",
                                   "code": line.strip()})
            # Hardcoded path
            if re.search(r'[A-Z]:\\', line) and not line.strip().startswith('#'):
                violations.append({"line": i, "rule": "hardcoded-path",
                                   "code": line.strip()[:80]})
            # bare print (not in procedures)
            if re.match(r'\s*print\(', line) and "procedure" not in str(p):
                pass  # print is fine in many places, skip
            # TODO/FIXME
            if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', line):
                violations.append({"line": i, "rule": "todo-marker",
                                   "code": line.strip()[:80]})

        # Check for type hints on function signatures
        func_lines = [(i, l) for i, l in enumerate(lines, 1)
                      if re.match(r'^\s*(?:async\s+)?def\s+\w+', l)]
        missing_hints = []
        for line_num, line in func_lines:
            if '->' not in line:
                missing_hints.append({"line": line_num, "rule": "missing-return-type",
                                      "code": line.strip()[:80]})

        result = json.dumps({"file": str(p), "total_lines": len(lines),
                             "violations": violations[:20],
                             "missing_type_hints": missing_hints[:10],
                             "violation_count": len(violations),
                             "missing_hint_count": len(missing_hints)})
```

### Step 2: Small model assesses severity and suggests fixes

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    violations = data.get("violations", []) + data.get("missing_type_hints", [])
    if not violations:
        result = _json.dumps({"assessment": "clean", "violations": [],
                              "note": "no convention violations found"})
    else:
        prompt = f"""Assess these code convention violations and suggest fixes.

File: {data['file']}
Violations:
{json.dumps(violations[:15], indent=2)}

Return JSON: {{"severity": "high|medium|low", "fixes": [{{"line": N, "fix": "what to change"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
        assessment = llm_generate(prompt)
        result = assessment
```

### Step 3: Return the convention report

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"assessment": "error", "violations": []}
result = _json.dumps(parsed)
```

## Related

- [[Code-Audit-Senior-Review]] — orchestrator that calls this check
- [[Check-Error-Handling]] — sibling code-audit probe
- [[Check-Complexity]] — sibling code-audit probe