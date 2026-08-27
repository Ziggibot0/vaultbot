---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Break a self-modification goal into a minimal ordered procedure step list. Given a goal like 'add a retry to the chat handler', reads the relevant code, identifies the exact functions to change, and returns a numbered list of surgical edits. Does NOT write code — just plans the edits so the big model can execute them one at a time.
when_to_use: before making a multi-file code change to the backend, when planning a self-modification, or when you need to know exactly which functions to touch before editing
falsifiable_if: the plan misses a function that needs changing, or includes functions that don't need changing
applies_to:
  - self-modification
  - code-planning
  - step-planning
  - surgical-editing
allowed_tools:
  - code_read
  - llm_generate
summary: "PROC-STEP-PLANER: Surgical backend code change checklist with [[Safe-Write]] model execution. Read file(s) to identify functions needing minimal edits and generate numbered list of changes via automat"
tags:
  - procedure
  - procedures
---

# Proc-Step-Planner

## When to Run This

Before making a code change to the backend, run this to get a surgical plan.
It reads the relevant code, finds the exact functions that need to change,
and returns a numbered list of minimal edits. The big model then executes
each edit with [[Safe-Write]].

## Why This Exists

Multi-file self-modification fails when the model edits blindly without knowing exactly which functions to touch. This procedure closes that gap by reading the target file and returning a numbered list of surgical edits before any code is written. The key tradeoff is that it only plans — it does not write code, so the big model executes each edit one at a time.

## Steps

### Step 1: Read the target file(s) and understand current structure

1. ```python
import json

goal = args.get("goal", "")
file_path = args.get("file_path", "")
if not goal or not file_path:
    result = json.dumps({"error": "goal and file_path arguments required"})
else:
    p = Path(file_path)
    if not p.exists():
        # Try resolving relative to backend
        p = Path(FRAMEWORK_ROOT) / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Extract function/class signatures for the model
        import re
        sigs = []
        for m in re.finditer(r'^(\s*(?:async\s+)?(?:def|class)\s+\w+.*)$', text, re.MULTILINE):
            line_num = text[:m.start()].count('\n') + 1
            sigs.append(f"  L{line_num}: {m.group(1).strip()}")
        result = json.dumps({"file": str(p), "total_lines": len(text.split('\n')),
                             "signatures": sigs[:40], "goal": goal})
```

### Step 2: Small model produces the surgical edit plan

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""You are a surgical code edit planner. Given a goal and a file's
function/class signatures, produce a MINIMAL ordered list of edits.

Goal: {data['goal']}
File: {data['file']}
Total lines: {data['total_lines']}
Functions/classes:
{chr(10).join(data['signatures'])}

For each edit, specify:
- Which function/class to modify (by name and line number)
- What to change (add, modify, remove)
- Why (one sentence)

Do NOT write the actual code. Just plan the edits.
Return JSON: {{"steps": [{{"target": "function_name (L##)", "action": "add|modify|remove", "what": "description", "why": "reason"}}], "files_affected": ["file.py"], "risk": "low|medium|high"}}
Return ONLY the JSON."""
    plan = llm_generate(prompt)
    result = plan
```

### Step 3: Return the plan

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"steps": [], "error": "could not parse plan"}
result = _json.dumps(parsed)
```

## Related

- [[Safe-Write]] — executes the edits this procedure plans
- [[Proc-Step-Summary]] — verifies each edit still imports cleanly
- [[Smart-Code-Read]] — maps a file's structure before planning edits