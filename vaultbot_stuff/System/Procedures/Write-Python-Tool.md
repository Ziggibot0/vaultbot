---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-30
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
description: "Create a new Python tool: audit existing capabilities, write code, test in sandbox, deploy, verify. 6 deterministic steps. Safe to run repeatedly (idempotent — won't create duplicates if tool already exists)."
falsifiable_if: "a tool created by following these steps fails to deploy, crashes on first use, or produces incorrect output"
applies_to:
  - tool-creation
  - self-improvement
  - coding
depends_on:
  - "[[Exemplar-Tool-Creation]]"
  - "[[Deterministic-Scaffolding-for-Small-Models]]"
sources:
  - "https://arxiv.org/abs/2605.28000v1"
  - "https://arxiv.org/abs/2507.10593v3"
  - "https://arxiv.org/abs/2508.13774v1"
allowed_tools:
  - code_read
  - code_run
  - vault_search
---

# Write-Python-Tool

## When to Run This

Run this procedure when you need to create a new tool — a capability you don't have that would help you or the operator. Covers the full lifecycle: identify the gap, write the code, test it, deploy it, verify it.

## Steps

### Step 1: Audit Existing Capabilities

Check whether a tool already exists for this task. Don't build duplicates.

1. ```python
import json

# The task description is passed in as `task_description` by the caller.
# If not provided, use a generic check.
task = task_description if 'task_description' in dir() else "general capability check"

# List all known tools from the system
known_tools = [
    "vault_search", "vault_list", "vault_append", "vault_safe_write",
    "vault_delete", "vault_lint", "vault_graph_analyzer", "vault_cluster_analyzer",
    "code_read", "code_run", "tool_create", "safe_write", "js_safe_write",
    "capability_audit", "self_reflect", "git_rollback", "preflight_safety_check",
    "vault_research", "web_read_source", "textbook_ingest", "textbook_read_page",
    "vault_gaps", "vaultbot_status", "set_goal", "plan_task", "update_task",
    "execute_procedure", "submit_contribution", "backend_restart", "plugin_reload",
]

result = json.dumps({
    "task": task,
    "known_tools": known_tools,
    "tool_count": len(known_tools),
    "action": "Review this list. If a tool covers the task, use it. If not, proceed to Step 2.",
})
```

[validate: at_least 1 tools listed]

### Step 2: Write the Tool Code

Draft the tool following the safety patterns: validate inputs first, handle errors gracefully, return structured dicts, keep it focused.

2. ```python
import json

# Tool code template — the caller fills in the actual implementation.
# This step is where the LLM writes the actual code based on the gap identified.
# The code MUST define a `run(args: dict) -> dict` function.

template = '''"""
Agent-authored tool: {tool_name}
"""

SCHEMA = {{
    "name": "{tool_name}",
    "description": "{description}",
    "parameters": {{
        "type": "object",
        "properties": {{
            {params}
        }},
        "required": {required}
    }}
}}


def run(args: dict) -> dict:
    """Tool implementation."""
    # 1. Validate inputs
    # 2. Handle errors with try/except
    # 3. Return structured dict
    result = {{"status": "ok"}}
    return result
'''

result = json.dumps({
    "status": "template_ready",
    "template_chars": len(template),
    "action": "Fill in the template with the actual implementation. Use code_run to test before deploying.",
})
```

### Step 3: Test in Sandbox

Run the code with `code_run` before deploying. Test happy path, error path, and edge cases.

3. ```python
import json

# This step is a gate — the actual test code is written by the LLM
# based on the specific tool being created. The validation ensures
# that testing was not skipped.

test_checklist = [
    "Normal inputs (happy path) — does run() return expected output?",
    "Missing required parameters — does run() return an error dict?",
    "Empty/None inputs — does run() handle gracefully?",
    "Wrong type inputs — does run() catch TypeError?",
]

result = json.dumps({
    "status": "test_required",
    "checklist": test_checklist,
    "action": "Run code_run with test cases for each checklist item. Do NOT proceed to Step 4 until all pass.",
})
```

[validate: contains "test_required"]

### Step 4: Deploy the Tool

Deploy using `tool_create`. The tool is immediately loaded and registered.

4. ```python
import json

# This step is executed by the LLM calling tool_create() directly.
# The procedure documents the requirement; the LLM performs the actual call.

result = json.dumps({
    "status": "deploy_ready",
    "action": "Call tool_create with: tool_name, description, parameters (JSON schema), code (the tested run() function). The tool loads immediately.",
    "safety": "If the tool edits backend .py files, use safe_write NOT code_run. Run preflight_safety_check first.",
})
```

### Step 5: Verify the Tool Works

Call the newly created tool with real inputs. Confirm it returns expected output.

5. ```python
import json

# This step is a gate — the LLM must call the new tool and verify output.

verification_checklist = [
    "Tool returns expected output with real inputs",
    "Error handling works with bad inputs (returns error dict, not crash)",
    "Tool shows up in capability_audit",
]

result = json.dumps({
    "status": "verify_required",
    "checklist": verification_checklist,
    "action": "Call the new tool with real inputs. If any check fails, fix the code and re-deploy.",
})
```

[validate: contains "verify_required"]

### Step 6: Record the Tool

Log the new tool in the vault for future reference and dedup checking.

6. ```python
import json, os, datetime

# Check if tool already logged (idempotent — skip if already exists)
tool_name = tool_name if 'tool_name' in dir() else "unknown_tool"
log_path = os.path.join(os.environ.get("VAULT_PATH", "."), "Memory", "Build-Log", "VaultBot-Build-Log.md")

already_logged = False
try:
    with open(log_path, encoding='utf-8') as f:
        if tool_name in f.read():
            already_logged = True
except:
    pass

result = json.dumps({
    "status": "skipped" if already_logged else "log_required",
    "tool_name": tool_name,
    "already_logged": already_logged,
    "action": "If not already logged, append to VaultBot-Build-Log.md under Tool Building section.",
})
```

## Common Failure Modes

| Failure | Fix |
|---|---|
| Untested code deployed | Always test with code_run first. Never skip Step 3. |
| Vague description | Write specific descriptions: "Use this when..." not "This tool does X" |
| No error handling | Wrap in try/except, return error dict with actionable message |
| Edits backend without safe_write | Always use safe_write for .py files. It auto-rolls-back bad edits. |

## Related

- [[Exemplar-Tool-Creation]] — worked example of creating vault_list
- [[Deterministic-Scaffolding-for-Small-Models]] — why deterministic validation matters
- [[Procedural-Bootstrap-and-Evolution-Plan]] — how this procedure fits in the larger framework
- [[Small-Model-Path-to-AGI]] — why simpler tool interfaces matter for 30B models