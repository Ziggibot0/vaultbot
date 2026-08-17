---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Route a task intent to the best procedure for it. Given a description of what the vaultbot is about to do, searches the procedure library and uses the small model to pick the single best-matching procedure by comparing the task intent to each procedure's when_to_use. Returns the procedure name or 'none'. Use when the big model is about to do something and wants to know if a procedure already handles it.
when_to_use: when the big model is about to start a task and wants to know which procedure to call, when deciding which procedure fits a step, or when the procedure surface didn't surface an obvious match
falsifiable_if: the router picks a procedure that doesn't apply to the task, or misses a procedure that obviously applies
applies_to:
  - procedure-routing
  - meta-procedure
  - self-modification
  - task-routing
allowed_tools:
  - vault_list
  - llm_generate
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Small-Model-Route

## When to Run This

This is the **meta-procedure** — a procedure for deciding on a procedure.
When the big model is about to do something and the procedure surface didn't
obviously surface a match, call this. It scans the procedure library and picks
the best one. Uses the small model (cheap, local) for the routing decision.

## Steps

### Step 1: List all procedures with their when_to_use

1. ```python
import json, re

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
    name = p.stem
    desc = ""
    when = ""
    for line in fm.split("\n"):
        if line.strip().startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
        if line.strip().startswith("when_to_use:") or line.strip().startswith("when:"):
            when = line.split(":", 1)[1].strip().strip('"').strip("'")
    procedures.append({"name": name, "description": desc[:120], "when_to_use": when[:150]})

result = json.dumps({"procedures": procedures, "count": len(procedures)})
```

### Step 2: Small model picks the best match for the task intent

2. ```python
import json as _json

intent = args.get("intent", "")
data = _json.loads(output)
procedures = data.get("procedures", [])

if not intent:
    result = _json.dumps({"error": "intent argument required"})
elif not procedures:
    result = _json.dumps({"best_match": "none", "reason": "no procedures found"})
else:
    proc_list = "\n".join(
        f"- {p['name']}: {p['when_to_use']}" for p in procedures)
    prompt = f"""You are a procedure router. Given a task intent, pick the SINGLE best
procedure from this list. Match by what the procedure DOES, not by word overlap.

Task intent: {intent}

Available procedures:
{proc_list}

Return JSON: {{"best_match": "Procedure-Name", "confidence": "high|medium|low", "reason": "why this procedure fits"}}
If no procedure fits, return {{"best_match": "none", "confidence": "high", "reason": "no procedure covers this task"}}
Return ONLY the JSON."""
    route = llm_generate(prompt)
    result = route
```

### Step 3: Return the routing decision

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"best_match": "none", "reason": "could not parse route"}
result = _json.dumps(parsed)
```
