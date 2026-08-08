---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-05
description: "Scan a Python file for mutable default argument anti-patterns: def f(x=[]), def f(x={}), def f(x=set()). These are shared across calls and cause subtle bugs. Use when auditing code quality or reviewing function signatures."
when_to_use: when auditing code quality, reviewing function signatures, checking for Python anti-patterns, or when asked 'are there mutable default arguments'
falsifiable_if: the procedure reports mutable defaults that aren't real, or misses actual mutable defaults
applies_to:
  - code-quality
  - convention-checking
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: Check-Mutable-Defaults
tags:
  - procedure
  - procedures
---

# Check-Mutable-Defaults

## When to Run This

Run this to catch mutable default arguments (`[]`, `{}`, `set()`) in
function signatures. These are a classic Python footgun: the default
object is shared across all calls, leading to state leakage.

## Steps

### Step 1: Read the file and scan for mutable defaults deterministically

1. ```python
import ast, json, re

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(vault_path) / file_path
    if not p.exists():
        p = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        findings = []
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for default in node.args.defaults + node.args.kw_defaults:
                        if default is None:
                            continue
                        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                            findings.append({
                                "line": node.lineno,
                                "function": node.name,
                                "rule": "mutable-default",
                                "detail": f"def {node.name}(...={ast.dump(default)})",
                                "code": text.split('\n')[node.lineno - 1].strip()[:80]
                            })
                        # Also check ListComp, DictComp, SetComp
                        if isinstance(default, (ast.ListComp, ast.DictComp, ast.SetComp)):
                            findings.append({
                                "line": node.lineno,
                                "function": node.name,
                                "rule": "mutable-default-comprehension",
                                "detail": f"def {node.name}(...=<comprehension>)",
                                "code": text.split('\n')[node.lineno - 1].strip()[:80]
                            })
        except SyntaxError as e:
            # Fallback to regex if AST parse fails
            lines = text.split('\n')
            for i, line in enumerate(lines, 1):
                if re.match(r'^\s*(?:async\s+)?def\s+\w+', line):
                    if re.search(r'=\s*\[\s*\]', line) or re.search(r'=\s*\{\s*\}', line) or re.search(r'=\s*set\(\)', line):
                        findings.append({
                            "line": i, "rule": "mutable-default-regex",
                            "detail": line.strip()[:80],
                            "code": line.strip()[:80]
                        })

        result = json.dumps({
            "file": str(p),
            "findings": findings[:20],
            "finding_count": len(findings)
        })
```

### Step 2: Small model assesses severity and suggests fixes

2. ```python
import json as _json
data = _json.loads(output)
if "error" in data:
    result = output
else:
    findings = data.get("findings", [])
    if not findings:
        result = _json.dumps({"assessment": "clean", "findings": [],
                              "note": "no mutable default arguments found"})
    else:
        prompt = f"""Assess these mutable default argument violations and suggest fixes.

File: {data['file']}
Findings:
{_json.dumps(findings[:15], indent=2)}

For each, suggest replacing the mutable default with None and initializing inside the function body.
Return JSON: {{"severity": "high|medium|low", "fixes": [{{"line": N, "fix": "what to change"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
        assessment = llm_generate(prompt)
        result = assessment
```

### Step 3: Return the mutable-defaults report

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"assessment": "error", "findings": []}
result = _json.dumps(parsed)
```