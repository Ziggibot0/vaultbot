---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Analyze a backend function's call flow: what calls it, what it calls, and what the full execution path looks like. Given a function name, traces the call graph up (callers) and down (callees) two levels. Use when understanding how a function fits into the system before modifying it."
when_to_use: before modifying a function to understand its place in the call graph, when debugging where a function gets called from, or when understanding execution flow
falsifiable_if: the trace misses callers or callees, or includes false references
applies_to:
  - code-comprehension
  - call-flow
  - self-modification
  - dependency-tracing
allowed_tools:
  - code_read
  - llm_generate
summary: Analyze-Function-Flow
tags:
  - procedure
  - procedures
---

# Analyze-Function-Flow

## When to Run This

Before modifying a function, understand its call graph. This traces
callers (who calls it) and callees (what it calls) two levels deep.

## Steps

### Step 1: Scan the backend for the call graph

1. ```python
import re, json

func_name = args.get("function_name", "")
if not func_name:
    result = json.dumps({"error": "function_name argument required"})
else:
    backend_dir = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend"
    # Find all function definitions and calls
    all_funcs = {}  # name -> {file, line, calls: [names]}
    for py in backend_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(py.relative_to(backend_dir))
        # Find definitions
        for m in re.finditer(r'^(\s*)((?:async\s+)?def\s+(\w+))', text, re.MULTILINE):
            name = m.group(3)
            line_num = text[:m.start()].count('\n') + 1
            # Find calls within this function's body (up to next def/class)
            body_start = m.start()
            next_def = re.search(r'^(\s*)(?:async\s+)?(?:def|class)\s+\w+', text[m.end():], re.MULTILINE)
            body_end = m.end() + next_def.start() if next_def else len(text)
            body = text[body_start:body_end]
            calls = set(re.findall(r'\b(\w+)\s*\(', body))
            calls.discard(name)  # don't include self
            if name not in all_funcs:
                all_funcs[name] = {"file": rel, "line": line_num, "calls": list(calls)[:15]}
            else:
                all_funcs[name]["calls"].extend(list(calls)[:15])

    # Trace callers (who calls the target)
    callers = []
    for name, info in all_funcs.items():
        if func_name in info["calls"]:
            callers.append({"caller": name, "file": info["file"], "line": info["line"]})
    # Trace callees (what the target calls)
    target_info = all_funcs.get(func_name, {})
    callees = target_info.get("calls", [])
    # Second level: what do the callees call?
    level2 = {}
    for callee in callees[:8]:
        if callee in all_funcs:
            level2[callee] = all_funcs[callee].get("calls", [])[:5]

    result = json.dumps({
        "target": func_name,
        "target_location": {"file": target_info.get("file"), "line": target_info.get("line")},
        "callers": callers[:15],
        "callees": callees[:15],
        "callees_level2": level2,
    })
```

### Step 2: Small model summarizes the flow

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    prompt = f"""Summarize this function's call flow in plain language.
Target: {data['target']} ({data['target_location']})
Called by: {data['callers']}
Calls: {data['callees']}
Second-level calls: {json.dumps(data['callees_level2'], indent=2)}

Return JSON: {{"flow_summary": "3-4 sentence description of the execution flow", "entry_points": ["how this function gets invoked"], "critical_path": ["the important downstream calls"]}}
Return ONLY the JSON."""
    summary = llm_generate(prompt)
    result = summary
```

### Step 3: Return the flow analysis

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"flow_summary": "could not parse"}
result = _json.dumps({"call_graph": data, "analysis": parsed})
```