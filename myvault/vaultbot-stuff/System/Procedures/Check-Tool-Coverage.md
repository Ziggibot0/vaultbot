---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Check if the vaultbot has the tools it needs for a task. Given a task description, lists all available tools (injected + custom), and the small model assesses whether the toolset covers the task. Returns missing capabilities and suggests which to create. Use when the vaultbot hits a wall or before starting a complex task.
when_to_use: when the vaultbot lacks a capability, before starting a complex task, when hitting a wall, when assessing what tools are needed, or when asked 'can I do this'
falsifiable_if: the assessment says tools are sufficient when they aren't, or says tools are missing when they exist
applies_to:
  - self-improvement
  - capability-audit
  - task-planning
  - tool-creation
allowed_tools:
  - code_read
  - llm_generate
summary: Check-Tool-Coverage
tags:
  - procedure
  - procedures
---

# Check-Tool-Coverage

## When to Run This

Before starting a complex task, run this to check if the vaultbot has the
tools it needs. If not, it identifies what's missing and suggests what to
create.

## Why This Exists

The vaultbot hits walls mid-task when it lacks a needed tool, and there was no way to assess coverage before starting. This procedure enumerates available tools and assesses whether they cover a task. The key tradeoff is that coverage assessment is delegated to the small model rather than hardcoding a capability matrix.

## Steps

### Step 1: Enumerate available tools

1. ```python
import json, re

# Scan backend for tool definitions
backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
tools = []
# Core tools from agent_tools
core_tools = ["vault_search", "code_read", "plan_task", "update_task",
              "execute_procedure", "vault_safe_write", "vault_append"]
tools.extend([{"name": t, "type": "core"} for t in core_tools])

# Custom tools
custom_dir = backend_dir / "custom_tools"
if custom_dir.exists():
    for py in custom_dir.glob("*.py"):
        if py.name.startswith("_") or py.name == "__init__.py":
            continue
        tools.append({"name": py.stem, "type": "custom", "file": str(py.name)})

# Procedure library as tools
proc_dir = Path(vault_path) / "vaultbot-stuff" / "System" / "Procedures"
proc_count = len(list(proc_dir.glob("*.md")))
tools.append({"name": f"{proc_count} procedures", "type": "procedure-library"})

result = json.dumps({"tools": tools, "total": len(tools)})
```

### Step 2: Small model assesses coverage for the task

2. ```python
import json as _json

task = args.get("task", "")
data = _json.loads(output)
tools = data.get("tools", [])

if not task:
    result = _json.dumps({"error": "task argument required"})
else:
    tool_list = "\n".join(f"- {t['name']} ({t['type']})" for t in tools)
    prompt = f"""Assess whether these tools cover this task:

Task: {task}

Available tools:
{tool_list}

Return JSON: {{"covered": true/false, "missing_capabilities": ["what's needed but not available"], "existing_tools_that_help": ["tool names"], "suggested_tool_to_create": "name and description if something is missing", "alternative_approach": "how to do it with existing tools if possible"}}
Return ONLY the JSON."""
    assessment = llm_generate(prompt)
    result = assessment
```

### Step 3: Return the coverage assessment

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"covered": False, "error": "could not parse assessment"}
result = _json.dumps(parsed)
```

## Related

- [[Capability-Audit]] — sibling capability assessment
- [[Write-Python-Tool]] — creates a tool to fill a gap
- [[Build-Procedure]] — creates a procedure to fill a gap