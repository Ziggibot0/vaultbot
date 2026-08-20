---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Scan a Python file for error-handling anti-patterns: bare excepts, silent swallowing (except: pass), overly broad catches (except Exception), and missing exception types. Returns line-numbered violations. Use when auditing code quality or before a commit."
when_to_use: when auditing error handling, before a commit, after editing a file, when checking for bare excepts or silent swallowing, or when asked 'does this code handle errors properly'
falsifiable_if: the procedure reports error-handling violations that aren't real, or misses actual violations
applies_to:
  - code-quality
  - error-handling
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: Check-Error-Handling
tags:
  - procedure
  - procedures
  - code-audit
---

# Check-Error-Handling

## When to Run This

Run this to scan a Python file for error-handling anti-patterns.
Catches bare `except:`, silent swallowing (`except: pass`),
overly broad `except Exception:`, and missing exception types.

## Why This Exists

Bare excepts and silent swallowing hide failures, but they slip into code unnoticed. This procedure scans a file for error-handling anti-patterns and returns line-numbered violations. The key tradeoff is that detection is deterministic (regex), while severity assessment and fix suggestions are delegated to the small model.

## Steps

### Step 1: Read the file and detect error-handling violations deterministically

1. ```python
import re, json

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(vault_path) / file_path
    if not p.exists():
        p = Path(vault_path) / "vaultbot" / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split('\n')
        violations = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Bare except (no exception type at all)
            if re.match(r'\s*except\s*:', line):
                violations.append({"line": i, "rule": "bare-except",
                                   "code": stripped[:80],
                                   "fix": "specify the exception type, e.g. except ValueError:"})
            # Silent swallowing: except ... : pass
            if re.match(r'\s*except.*:\s*pass\s*$', line):
                violations.append({"line": i, "rule": "silent-swallow",
                                   "code": stripped[:80],
                                   "fix": "log or re-raise instead of silently passing"})
            # Broad catch: except Exception (too generic)
            if re.match(r'\s*except\s+Exception\s*(?:\s+as\s+\w+)?\s*:', line):
                violations.append({"line": i, "rule": "broad-catch",
                                   "code": stripped[:80],
                                   "fix": "catch specific exception types instead of Exception"})
            # Bare except with pass on next line (common pattern)
            if i < len(lines) and re.match(r'\s*except\s*:', line):
                next_stripped = lines[i].strip() if i < len(lines) else ""
                if next_stripped == "pass":
                    violations.append({"line": i, "rule": "bare-except-pass",
                                       "code": stripped[:80],
                                       "fix": "don't silently swallow exceptions — log or re-raise"})

        result = json.dumps({"file": str(p), "total_lines": len(lines),
                             "violations": violations[:30],
                             "violation_count": len(violations)})
```

### Step 2: Small model assesses violations and suggests fixes

2. ```python
import json as _json
data = _json.loads(output)
if "error" in data:
    result = output
else:
    violations = data.get("violations", [])
    if not violations:
        result = _json.dumps({"assessment": "clean", "violations": [],
                              "note": "no error-handling violations found"})
    else:
        prompt = f"""Assess these error-handling violations and suggest fixes.

File: {data['file']}
Violations:
{json.dumps(violations[:15], indent=2)}

Return JSON: {{"severity": "high|medium|low", "fixes": [{{"line": N, "fix": "what to change"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
        assessment = llm_generate(prompt)
        result = assessment
```

### Step 3: Return the error-handling report

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
- [[Check-Resource-Leaks]] — sibling code-audit probe
- [[Code-Structure-Check]] — sibling convention check