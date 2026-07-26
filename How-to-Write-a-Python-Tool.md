---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
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
---

# How to Write a Python Tool

## When to Use This

Use this procedure when you need to create a new tool for yourself — a capability you don't have that would help you or Sean. This covers the full lifecycle: identifying the gap, writing the code, testing it, deploying it, and verifying it works.

## Steps

### Step 1: Audit Existing Capabilities

Run `capability_audit` with the task you're trying to accomplish. This tells you whether a tool already exists for this purpose.

**If a tool already exists** → Use it. Don't build a duplicate.
**If there's a capability gap** → Continue to Step 2.

### Step 2: Propose the Tool

Run `self_reflect` with a description of the capability you need. This returns concrete proposals with code sketches.

Review the proposals. Pick the simplest one that solves the problem. Simplicity is not optional — it's a requirement for a 30B model to maintain the tool [sources: Tool Forge: A Validation-Carrying Toolchain for Governed Agentic Execution].

### Step 3: Write the Tool Code

Write the `run(args: dict) -> dict` function. Follow these rules:

1. **Define a clear schema** — Every parameter has a type, a description, and whether it's required. The schema IS the documentation for the model [sources: Agentic DraCor and the Art of Docstring Engineering].
2. **Validate inputs first** — Check all required parameters exist and have the right type before doing anything else. Return an error dict if validation fails: `{"error": "Missing required parameter: 'file_path'"}`
3. **Handle errors gracefully** — Wrap risky operations in try/except. Never let a tool crash without returning a useful error message.
4. **Return structured data** — Always return a dict with clear keys. Never return a bare string or None.
5. **Keep it focused** — One tool does one thing. If you need complex behavior, chain multiple tools together.
6. **Write descriptive docstrings** — The tool description is what the model reads to decide when to use it. Be specific about what it does and when to use it [sources: Agentic DraCor and the Art of Docstring Engineering].

### Step 4: Test in Sandbox

Run `code_run` with the tool code. Test with:
- Normal inputs (happy path)
- Missing required parameters (error path)
- Empty/None inputs (edge case)
- Wrong type inputs (type error path)

**If any test fails** → Fix the code and re-test. Do not deploy untested code.
**If all tests pass** → Continue to Step 5.

### Step 5: Deploy the Tool

Run `tool_create` with the tool name, description, parameter schema, and tested code.

The tool is immediately loaded and registered — you can call it in the very next turn.

### Step 6: Verify the Tool Works

Call the newly created tool with real inputs. Confirm:
- The tool returns expected output
- Error handling works with bad inputs
- The tool shows up in `capability_audit`

### Step 7: Run Preflight Check (If Editing Source)

If the tool requires editing backend source code (`.py` files under `vaultbot_backend/`):
1. Run `preflight_safety_check` first
2. Use `safe_write` (NOT `code_write`) — it syntax-checks and auto-rolls-back if the edit would break the backend
3. Verify the backend still imports cleanly after the edit

## What the Research Says

Tool Forge [sources: Tool Forge: A Validation-Carrying Toolchain for Governed Agentic Execution] treats tools as capsules containing intent, capability contract, implementation, tests, documentation, and validation evidence. Their framework achieves 94% micro-F1 against deterministic acceptance checks by treating tool creation as a governed pipeline, not free-form coding.

ToolRegistry [sources: ToolRegistry: A Protocol-Agnostic Tool Management Library for Function-Calling LLMs] emphasizes protocol-agnostic tool management — tools should work regardless of the calling framework. VaultBot's `tool_create` already handles this by registering tools for both internal and MCP use.

Docstring engineering [sources: Agentic DraCor and the Art of Docstring Engineering] shows that tool documentation quality directly impacts LLM tool selection accuracy. The description and parameter descriptions are not afterthoughts — they are the primary interface between the model and the tool.

## Common Failure Modes

| Failure | What happens | How to fix |
|---|---|---|
| **Untested code deployed** | Tool crashes on first real use | Always test with `code_run` first. Never skip Step 4. |
| **Vague description** | Model never calls the tool or calls it at wrong times | Write specific descriptions: "Use this when..." not "This tool does X" |
| **Too many parameters** | Model provides wrong arguments or omits required ones | Keep parameters to 1-3. Split complex tools into simpler ones. |
| **No error handling** | Tool crashes instead of returning useful error | Wrap in try/except, return error dict with actionable message |
| **Edits backend without safe_write** | Backend breaks, VaultBot dies | Always use `safe_write` for .py files. It auto-rolls-back bad edits. |

## Validation Criteria

This procedure is working correctly when:
- Tools created by following these steps deploy successfully on the first try
- Tools handle bad inputs gracefully (error dict, not crash)
- The model correctly identifies when to use the tool based on its description
- No backend-breaking edits are made (safe_write catches them)

## Related

- [[Exemplar-Tool-Creation]] — worked example of creating vault_list
- [[Deterministic-Scaffolding-for-Small-Models]] — why deterministic validation matters
- [[Procedural-Bootstrap-and-Evolution-Plan]] — how this procedure fits in the larger framework
- [[Small-Model-Path-to-AGI]] — why simpler tool interfaces matter for 30B models
