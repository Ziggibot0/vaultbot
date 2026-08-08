---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-05
description: "Scan a Python file for resource-leak anti-patterns: open() calls without with-blocks, socket/database connections not closed, missing finally cleanup. Returns structured findings with line numbers and suggested fixes."
when_to_use: when auditing code for resource safety, before a commit, after editing file I/O code, or when asked 'does this code leak resources'
falsifiable_if: the procedure reports leaks that aren't real, or misses actual leaks
applies_to:
  - code-quality
  - resource-safety
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: Check-Resource-Leaks
tags:
  - procedure
  - procedures
  - code-audit
---

# Check-Resource-Leaks

## When to Run This

Run this to check if a backend file has resource-leak patterns:
`open()` without `with`, socket or DB connections without `close()`,
missing `finally` cleanup. Catches leaks before they exhaust file
descriptors or connections in production.

## Steps

### Step 1: Read the file and scan for resource-leak patterns deterministically

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
        p = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split('\n')
        leaks = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # open() without with — look for open( not preceded by with
            if re.search(r'(?<!with\s)\bopen\s*\(', line) and 'with' not in line:
                leaks.append({"line": i, "rule": "open-without-with",
                               "code": stripped[:80]})
            # .connect( without close in surrounding context
            if re.search(r'\.connect\s*\(', stripped) and 'close' not in stripped:
                leaks.append({"line": i, "rule": "connect-without-close",
                               "code": stripped[:80]})
            # socket.socket( without close
            if re.search(r'socket\.socket\s*\(', stripped) and 'close' not in stripped:
                leaks.append({"line": i, "rule": "socket-without-close",
                               "code": stripped[:80]})
            # cursor() without close
            if re.search(r'\.cursor\s*\(', stripped) and 'close' not in stripped:
                leaks.append({"line": i, "rule": "cursor-without-close",
                               "code": stripped[:80]})

        result = json.dumps({"file": str(p), "total_lines": len(lines),
                             "leaks": leaks[:20],
                             "leak_count": len(leaks)})
```

### Step 2: Small model assesses severity and suggests fixes

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    leaks = data.get("leaks", [])
    if not leaks:
        result = _json.dumps({"assessment": "clean", "leaks": [],
                              "note": "no resource leaks found"})
    else:
        prompt = f"""Assess these resource-leak findings and suggest fixes.

File: {data['file']}
Leaks:
{json.dumps(leaks[:15], indent=2)}

Return JSON: {{"severity": "high|medium|low", "fixes": [{{"line": N, "fix": "what to change"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
        assessment = llm_generate(prompt)
        result = assessment
```

### Step 3: Return the resource-leak report

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"assessment": "error", "leaks": []}
result = _json.dumps(parsed)
```