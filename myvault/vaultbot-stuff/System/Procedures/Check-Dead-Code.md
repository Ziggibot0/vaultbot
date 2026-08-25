---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Scan a Python file for dead-code anti-patterns: unused imports, unreachable code after return/break/continue/raise, and variables assigned but never used. Returns structured findings with line numbers and suggestions."
when_to_use: when auditing code quality for dead code, before a commit, after refactoring, or when asked 'are there unused imports or unreachable branches'
falsifiable_if: the procedure reports dead code that is actually used, or misses real dead code
applies_to:
  - code-quality
  - dead-code-detection
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: Check-Dead-Code
tags:
  - procedure
  - procedures
---

# Check-Dead-Code

## When to Run This

Run this to find dead code in a Python file: unused imports,
unreachable statements after return/break/continue/raise, and
variables that are assigned but never referenced. Dead code adds
maintenance burden and obscures intent.

## Why This Exists

Dead code — unused imports, unreachable statements, and never-referenced variables — adds maintenance burden and obscures intent, but it accumulates silently. This procedure AST-scans a file to surface it with line numbers. The key tradeoff is that detection is deterministic (AST), while severity assessment and fix suggestions are delegated to the small model.

## Steps

### Step 1: AST scan for unused imports and unreachable code

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
            result = json.dumps({"error": f"syntax error: {e}"})
        else:
            findings = []

            # --- Unused imports ---
            imported_names = {}  # name -> line
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        imported_names[local] = node.lineno
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        local = alias.asname or alias.name
                        imported_names[local] = node.lineno

            # Collect all names used anywhere (Load context)
            used_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    used_names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    # attribute access on imported module
                    if isinstance(node.value, ast.Name):
                        used_names.add(node.value.id)

            for name, line in imported_names.items():
                if name not in used_names:
                    findings.append({"line": line, "rule": "unused-import",
                                     "detail": f"'{name}' imported but never used"})

            # --- Unreachable code after return/break/continue/raise ---
            class UnreachableFinder(ast.NodeVisitor):
                def __init__(self):
                    self.unreachable = []

                def _check_body(self, body):
                    for i, stmt in enumerate(body):
                        if isinstance(stmt, (ast.Return, ast.Break,
                                             ast.Continue, ast.Raise)):
                            if i + 1 < len(body):
                                next_stmt = body[i + 1]
                                self.unreachable.append({
                                    "line": next_stmt.lineno,
                                    "rule": "unreachable-code",
                                    "detail": f"statement after {type(stmt).__name__} on line {stmt.lineno}"
                                })
                            break

                def visit_FunctionDef(self, node):
                    self._check_body(node.body)
                    self.generic_visit(node)

                def visit_AsyncFunctionDef(self, node):
                    self._check_body(node.body)
                    self.generic_visit(node)

                def visit_For(self, node):
                    self._check_body(node.body)
                    self._check_body(node.orelse)
                    self.generic_visit(node)

                def visit_While(self, node):
                    self._check_body(node.body)
                    self._check_body(node.orelse)
                    self.generic_visit(node)

                def visit_If(self, node):
                    self._check_body(node.body)
                    self._check_body(node.orelse)
                    self.generic_visit(node)

            finder = UnreachableFinder()
            finder.visit(tree)
            findings.extend(finder.unreachable)

            result = json.dumps({"file": str(p), "findings": findings[:30],
                                 "finding_count": len(findings)})
```

### Step 2: LLM assess severity and suggest fixes

2. ```python
import json as _json

try:
    data = _json.loads(output)
except Exception:
    data = {"error": "failed to parse step 1 output"}

if "error" in data:
    result = output
elif not data.get("findings"):
    result = _json.dumps({"assessment": "clean", "findings": [],
                          "note": "no dead-code patterns found"})
else:
    prompt = f"""Assess these dead-code findings and suggest fixes.

File: {data['file']}
Findings:
{_json.dumps(data['findings'][:15], indent=2)}

Return JSON: {{"severity": "high|medium|low", "fixes": [{{"line": N, "fix": "what to change"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
    assessment = llm_generate(prompt)
    result = assessment
```

### Step 3: Return the dead-code report

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
- [[Check-Complexity]] — sibling code-audit probe
- [[Find-Dead-Code]] — vault-wide dead code scan