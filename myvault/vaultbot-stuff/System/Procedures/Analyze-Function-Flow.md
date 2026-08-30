---
type: procedure
status: experimental
baseline: true
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
  - code_semantic
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

## Why This Exists

Modifying a function without knowing its place in the call graph risks
breaking callers or missing downstream effects. This procedure traces
callers and callees so edits are made with full context.
The key tradeoff is that resolution is semantic: it uses the
`code_semantic` tool (jedi-powered cross-file static analysis) rather
than a regex scan, so a name is only counted as a caller when it is a
real call — a comment or string literal mentioning the name is not a
call, and cross-module imports resolve to the real definition.

## Steps

### Step 1: Resolve the call graph with code_semantic

1. ```python
import json

func_name = args.get("function_name", "")
if not func_name:
    result = json.dumps({"error": "function_name argument required"})
else:
    # code_semantic resolves definitions, callers, and callees across
    # modules using jedi. Pass the bare function name (optionally dotted,
    # e.g. 'code_verify.verify_import_targets').
    callers_res = code_semantic("callers", func_name)
    callees_res = code_semantic("callees", func_name)
    define_res = code_semantic("define", func_name)

    def _loc(d):
        mod = (d.get("module_path") or "").replace("\\", "/")
        line = d.get("line")
        return f"{mod}:{line}" if mod and line else mod or d.get("full_name") or d.get("name")

    target_loc = None
    for d in define_res.get("definitions", []):
        target_loc = _loc(d)
        break

    callers = []
    for c in callers_res.get("callers", []):
        ctx = c.get("context") or {}
        callers.append({
            "caller": ctx.get("name") or _loc(c),
            "kind": ctx.get("kind"),
            "file_line": _loc(c),
            "module_path": c.get("module_path"),
        })

    callees = set()
    for c in callees_res.get("callees", []):
        callees.update(c.get("calls", []))

    data = {
        "target": func_name,
        "target_location": {"file_line": target_loc},
        "callers": callers,
        "callees": sorted(callees),
    }
    result = json.dumps(data)
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

## Related

- [[Codebase-Map]] — static index of the backend this traces through
- [[Analyze-Session-Log]] — reconstructs runtime behavior from logs
- [[Code-Structure-Check]] — sibling code-audit probe