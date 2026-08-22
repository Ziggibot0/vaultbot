---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-08-02
description: Check whether a task already has a procedure (single-task mode), or audit the entire procedure library for coverage gaps (full_audit mode). In single-task mode, searches the procedure library by intent and returns whether a procedure exists. In full_audit mode, lists all procedures with their coverage scope and flags gaps where no procedure exists for a known recurring task. Replaces the former Procedure-Library-Audit.
when_to_use: when the vaultbot is about to do something and should check if a procedure already handles it, before doing manual tool calls, when deciding procedure vs manual, or when auditing the procedure library for completeness
falsifiable_if: the check says a procedure exists when it doesn't fit, or says no procedure exists when one does, or the full audit misses obvious gaps
applies_to:
  - procedure-routing
  - meta-procedure
  - self-improvement
  - task-checking
  - library-audit
allowed_tools:
  - vault_list
  - llm_generate
success_count: 4
failure_count: 1
success_rate: 0.8
summary: Procedure-Coverage-Check
tags:
  - procedure
  - procedures
---

# Procedure-Coverage-Check

## When to Run This

Quick check: does a procedure already exist for what I'm about to do?
Lighter than [[Small-Model-Route]] — just a yes/no with the procedure name.

In `full_audit` mode, this also replaces the former Procedure-Library-Audit:
it walks every procedure, extracts its coverage scope, and flags tasks that
recurring in chat history but have no procedure covering them.

## Why This Exists

Before doing manual tool calls, the vaultbot needs to know whether a procedure already handles the task. This procedure closes that gap with a quick yes/no coverage check, and in `full_audit` mode audits the whole library for gaps. The tradeoff is that it is deliberately lighter than [[Small-Model-Route]] — just a yes/no with the procedure name rather than a full routing decision.

## Arguments

- `task` (string): The task to check coverage for. Required for single-task mode.
- `mode` (string): `"check"` (default) for single-task, `"full_audit"` for library-wide audit.

## Steps

### Step 1: List all procedures with descriptions

1. ```python
import json

proc_dir = Path(vault_path) / "vaultbot" / "System" / "Procedures"
procedures = []
for p in proc_dir.glob("*.md"):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if not text.startswith("---"):
        continue
    end = text.find("---", 3)
    if end == -1:
        continue
    fm = text[3:end]
    if "type: procedure" not in fm:
        continue
    desc = ""
    when = ""
    for line in fm.split("\n"):
        if line.strip().startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
        if line.strip().startswith("when_to_use:") or line.strip().startswith("when:"):
            when = line.split(":", 1)[1].strip().strip('"').strip("'")
    procedures.append({"name": p.stem, "description": desc[:150], "when_to_use": when[:150]})

result = json.dumps({"procedures": procedures, "count": len(procedures)})
```

### Step 2: Route based on mode

2. ```python
import json as _json

mode = args.get("mode", "check")
data = _json.loads(output)
procedures = data.get("procedures", [])

if mode == "full_audit":
    # Full library audit: list all procedures with scope, flag gaps
    proc_list = "\n".join(f"- {p['name']}: {p['when_to_use']}" for p in procedures)
    prompt = f"""You are auditing a procedure library for coverage gaps.

Here are all {len(procedures)} procedures with their when_to_use:
{proc_list}

Identify:
1. Procedures that overlap significantly (same intent, different names)
2. Common vaultbot tasks that have NO procedure covering them
3. Procedures whose scope is too narrow or too broad

Return JSON: {{"overlaps": [{{"a": "Proc1", "b": "Proc2", "reason": "why"}}], "gaps": ["task with no procedure", ...], "scope_issues": [{{"procedure": "Name", "issue": "what"}}]}}
Return ONLY the JSON."""
    audit = llm_generate(prompt)
    result = audit
else:
    # Single-task coverage check
    task = args.get("task", "")
    if not task:
        result = _json.dumps({"error": "task argument required for check mode"})
    else:
        proc_list = "\n".join(f"- {p['name']}: {p['when_to_use']}" for p in procedures)
        prompt = f"""Does any existing procedure cover this task?

Task: {task}

Available procedures:
{proc_list}

Return JSON: {{"covered": true/false, "procedure": "Procedure-Name or null", "confidence": "high|medium|low", "reason": "why"}}
Return ONLY the JSON."""
        check = llm_generate(prompt)
        result = check
```

### Step 3: Return the result

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"covered": False, "reason": "could not parse"}
result = _json.dumps(parsed)
```

## Related

- [[Small-Model-Route]] — the heavier routing decision this check is lighter than
- [[Procedure-Library-Index]] — the catalog of procedures this check searches
- [[Procedure-Eval]] — scores procedure health alongside coverage