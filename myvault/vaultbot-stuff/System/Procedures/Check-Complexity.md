---
type: procedure
status: experimental
baseline: true
created: 2026-08-05
description: "Scan a Python file for complexity anti-patterns: functions that are too long (>50 lines), excessive nesting depth (>4 levels), too many branches (if/elif/else chains). Returns structured report with severity and fix suggestions."
when_to_use: when auditing code complexity, when reviewing a file for maintainability, when checking if functions are too long or deeply nested, or when asked 'is this code too complex'
falsifiable_if: the procedure reports complexity issues that aren't real, or misses actual complexity problems
applies_to:
  - code-quality
  - complexity-analysis
  - maintainability
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: Check-Complexity
tags:
  - procedure
  - procedures
---

# Check-Complexity

## When to Run This

Run this to check if a Python file has complexity issues — functions
that are too long, deeply nested, or have too many branches. These
make code hard to maintain and test.

## Why This Exists

Long, deeply nested, or branch-heavy functions are hard to maintain and test, but there was no single check to flag them before a commit. This procedure AST-scans a file for function length, nesting depth, and branch count. The key tradeoff is that detection is deterministic (AST), while severity assessment and refactoring suggestions are delegated to the small model.

## Steps

### Step 1: AST-scan for function length, nesting depth, and branch count

1. ```python
import ast, json

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(vault_path) / file_path
    if not p.exists():
        p = Path(FRAMEWORK_ROOT) / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            result = json.dumps({"error": f"AST parse failed: {e}"})
        else:
            findings = []

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Function length (count lines from def to end of body)
                    start_line = node.lineno
                    end_line = node.end_lineno if hasattr(node, 'end_lineno') else start_line
                    func_length = end_line - start_line + 1

                    # Nesting depth: max depth of nested control-flow blocks
                    def max_depth(n, current=0):
                        d = current
                        for child in ast.iter_child_nodes(n):
                            if isinstance(child, (ast.If, ast.For, ast.While,
                                                 ast.With, ast.Try, ast.ExceptHandler)):
                                d = max(d, max_depth(child, current + 1))
                            else:
                                d = max(d, max_depth(child, current))
                        return d

                    depth = max_depth(node)

                    # Branch count: count if/elif/else/for/while/try/except
                    branches = sum(1 for n in ast.walk(node)
                                   if isinstance(n, (ast.If, ast.For, ast.While,
                                                    ast.Try, ast.ExceptHandler)))

                    name = node.name
                    issues = []
                    if func_length > 50:
                        issues.append({"type": "long-function",
                                       "detail": f"{func_length} lines (threshold: 50)"})
                    if depth > 4:
                        issues.append({"type": "deep-nesting",
                                       "detail": f"depth {depth} (threshold: 4)"})
                    if branches > 10:
                        issues.append({"type": "too-many-branches",
                                       "detail": f"{branches} branches (threshold: 10)"})

                    if issues:
                        findings.append({
                            "function": name,
                            "line": start_line,
                            "length": func_length,
                            "depth": depth,
                            "branches": branches,
                            "issues": issues
                        })

            result = json.dumps({
                "file": str(p),
                "functions_checked": sum(1 for n in ast.walk(tree)
                                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
                "findings": findings[:20],
                "finding_count": len(findings)
            })
```

### Step 2: LLM assess severity and suggest refactoring fixes

2. ```python
import json as _json
try:
    data = _json.loads(output)
except Exception:
    data = {"error": "failed to parse step 1 output"}

if "error" in data:
    result = output
else:
    findings = data.get("findings", [])
    if not findings:
        result = _json.dumps({"assessment": "clean", "findings": [],
                              "note": "no complexity issues found"})
    else:
        prompt = f"""Assess these code complexity findings and suggest refactoring fixes.

File: {data['file']}
Findings:
{_json.dumps(findings[:15], indent=2)}

Return JSON: {{"severity": "high|medium|low", "fixes": [{{"function": "name", "fix": "how to refactor"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
        assessment = llm_generate(prompt)
        result = assessment
```

### Step 3: Return the complexity report

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

## Related

- [[Code-Audit-Senior-Review]] — orchestrator that calls this check
- [[Check-Dead-Code]] — sibling code-audit probe
- [[Code-Structure-Check]] — sibling convention check