---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Verify that a procedure's code steps actually work — the imports resolve, the tools are in allowed_tools, and the args the steps reference are documented. Reads a procedure note, extracts its code steps, and checks each for common runtime failure patterns. Use when auditing procedures or before trusting a new procedure.
when_to_use: when auditing procedures for runtime safety, before trusting a new procedure, when a procedure keeps failing, or when asked 'will this procedure actually work'
falsifiable_if: the procedure reports issues that aren't real, or misses actual runtime problems
applies_to:
  - procedure-quality
  - procedure-audit
  - verification
  - self-improvement
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
summary: Verify-Procedure-Args
tags:
  - procedure
  - procedures
---

# Verify-Procedure-Args

## When to Run This

Run this to check if a procedure's code steps will actually work. Catches
common failure patterns: referencing tools not in allowed_tools, using
args that aren't documented, importing modules that don't exist.

## Steps

### Step 1: Parse the procedure and extract code steps + frontmatter

1. ```python
import re, json, os
from pathlib import Path

vault_path = os.environ.get("VAULT_PATH", ".")

proc_path = args.get("procedure_path", args.get("note_path", ""))
if not proc_path:
    result = json.dumps({"error": "procedure_path or note_path argument required"})
else:
    p = Path(vault_path) / "vaultbot" / "System" / "Procedures" / proc_path
    if not p.exists() and not proc_path.endswith(".md"):
        p = Path(vault_path) / "vaultbot" / "System" / "Procedures" / f"{proc_path}.md"
    if not p.exists():
        p = Path(proc_path)
    if not p.exists():
        result = json.dumps({"error": f"procedure not found: {proc_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Parse frontmatter
        fm = {}
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm_text = text[3:end]
                # Extract allowed_tools (could be list or inline)
                tools_match = re.search(r'allowed_tools:\s*\n((?:\s+-\s+.*\n)*)', fm_text)
                if tools_match:
                    fm["allowed_tools"] = re.findall(r'-\s+(\w+)', tools_match.group(1))
                else:
                    tools_match = re.search(r'allowed_tools:\s*\[(.*?)\]', fm_text)
                    if tools_match:
                        fm["allowed_tools"] = [t.strip() for t in tools_match.group(1).split(",")]
        # Extract code steps
        code_blocks = re.findall(r'```python\n(.*?)```', text, re.DOTALL)
        result = json.dumps({"procedure": p.stem, "allowed_tools": fm.get("allowed_tools", []),
                             "code_steps": [cb[:500] for cb in code_blocks[:5]],
                             "step_count": len(code_blocks)})
```

### Step 2: Small model checks for runtime issues

2. ```python
import json
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    allowed = data.get("allowed_tools", [])
    steps = data.get("code_steps", [])
    prompt = f"""Check this procedure's code steps for runtime issues:

Procedure: {data['procedure']}
Allowed tools: {allowed}

Code steps:
{json.dumps(steps, indent=2)}

Check for:
1. Using a tool that's NOT in allowed_tools
2. Referencing args keys that aren't documented
3. Importing modules that might not exist
4. Using bare names that aren't injected or imported
5. Missing result = json.dumps(...) at the end

Return JSON: {{"issues": [{{"step": N, "issue": "description", "severity": "error|warning"}}], "verdict": "safe|needs-fixes|broken"}}
Return ONLY the JSON."""
    check = llm_generate(prompt)
    result = check
```

### Step 3: Return the verification

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"issues": [], "verdict": "error"}
result = _json.dumps(parsed)
```