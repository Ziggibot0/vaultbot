---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Read a Python function and produce a concise summary of what it does, its inputs, outputs, and side effects. Given a file path and function name, extracts the function body and has the small model summarize it. Use when you need to understand a function before editing it, or when documenting code.
when_to_use: when you need to understand what a specific function does before editing it, when documenting a function, or when you encounter unfamiliar code
falsifiable_if: the summary contradicts what the function actually does, or fabricates behavior not in the code
applies_to:
  - code-comprehension
  - documentation
  - self-modification
  - verification
allowed_tools:
  - code_read
  - llm_generate
summary: Summarize-Function
tags:
  - procedure
  - procedures
---

# Summarize-Function

## When to Run This

Run this when you need to understand a specific function before touching it.
The small model reads the function body and produces a tight summary:
inputs, outputs, side effects, what it does.

## Steps

### Step 1: Extract the function from the file

1. ```python
import re, json

file_path = args.get("file_path", "")
func_name = args.get("function_name", "")
if not file_path or not func_name:
    result = json.dumps({"error": "file_path and function_name arguments required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(vault_path) / "vaultbot" / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Find the function (async def or def)
        pattern = rf'((?:async\s+)?def\s+{re.escape(func_name)}\s*\(.*?\).*?:.*?)(?=\n(?:async\s+)?def\s|\nclass\s|\Z)'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            func_body = match.group(1)
            start_line = text[:match.start()].count('\n') + 1
            end_line = start_line + func_body.count('\n')
            result = json.dumps({"function": func_name, "file": str(p),
                                 "start_line": start_line, "end_line": end_line,
                                 "body": func_body[:2000]})
        else:
            result = json.dumps({"error": f"function {func_name} not found in {file_path}"})
```

### Step 2: Small model summarizes the function

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Summarize this Python function concisely.
Function: {data['function']} ({data['file']} L{data['start_line']}-{data['end_line']})

{data['body']}

Return JSON:
{{"purpose": "one sentence", "inputs": "what arguments it takes", "outputs": "what it returns", "side_effects": "any I/O, mutations, or external calls", "dependencies": ["what other functions/modules it calls"]}}
Return ONLY the JSON."""
    summary = llm_generate(prompt)
    result = summary
```

### Step 3: Return the summary

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"error": "could not parse summary"}
result = _json.dumps(parsed)
```