---
type: procedure
status: experimental
baseline: true
created: 2026-08-31
description: "The sanctioned path for editing VaultBot's own backend Python code. Composes codebase orientation, narrow-scope reading, targeted editing, and verification to minimize the risk of breaking the system."
when_to_use: "When you need to modify any .py file in the vaultbot_backend directory, when fixing a bug in a tool, or when implementing a requested backend feature."
falsifiable_if: "The procedure allows an edit that breaks the backend without triggering the verification step, or it fails to follow the required tool sequence (map -> narrow read -> edit -> verify)."
applies_to:
  - self-modification
  - backend-maintenance
  - code-quality
allowed_tools:
  - codebase_map
  - code_read
  - edit_lines
  - llm_generate
summary: "Sanctioned self-edit path: codebase_map orient -> code_read narrow window -> edit_lines -> Verify-Backend-Change."
tags:
  - procedure
  - procedures
  - self-modification
---

# Self-Edit-Backend-Code

## Purpose

To provide a deterministic, safe, and narrow-scope sequence for modifying
the backend source code. This prevents "blind edits" and reduces the blast
radius of changes by forcing orientation and verification.

## Why This Exists

Observed 2026-08-31: when the model edited backend code without orientation
it regenerated whole files, corrupted escapes, and touched 100+ unrelated
notes in one session; context bloat (134k tokens from whole-file reads)
corrupted its tool calls. The fix is structural — force the cheap
orientation step (codebase_map), bound the read window so the model edits
from ground truth it can actually hold, and require verification before the
turn ends. Verified in practice: the nudge_scheduler UTC date bug was
diagnosed and fixed through this exact sequence on its first post-bootstrap
run.

depends_on:
  - codebase_map
  - code_read
  - edit_lines

## Inputs

- `file_path` (required): Path to the Python file to edit.
- `start_line` (required): The first line of the range to be replaced.
- `end_line` (required): The last line of the range to be replaced.
- `new_content` (required): The Python code to insert.
- `plan` (optional): A brief explanation of what is being changed and why.

## Steps

### Step 1: Orient via Codebase Map

```python
import json
import os

# Orient by reading the map for the specific module.
# We extract the module name from the file path.
module_name = os.path.basename(args.get("file_path", "")).replace(".py", "")
result = codebase_map(module=module_name)
print(json.dumps({"module_map": result}))
```
### Step 2: Narrow-Scope Read

```python
import json

# Read the specific lines to edit plus 20 lines of context on either side.
start = args.get("start_line", 1)
end = args.get("end_line", 1)
file_path = args.get("file_path", "")

read_start = max(1, start - 20)
read_end = end + 20

result = code_read(file_path=file_path, start_line=read_start, end_line=read_end)
print(json.dumps({"context_code": result}))
```
### Step 3: Targeted Edit

```python
import json

# Apply the edit to the narrow range specified in args.
result = edit_lines(
    file_path=args.get("file_path", ""),
    start_line=args.get("start_line"),
    end_line=args.get("end_line"),
    new_content=args.get("new_content"),
)
print(json.dumps({"edit_result": result}))
```
### Step 4: Verify Change

```python
import json

# Run the Verify-Backend-Change procedure to ensure no import errors
# were introduced by this edit.
result = run_procedure("Verify-Backend-Change", {"file_path": args.get("file_path")})
print(json.dumps({"verification": result}))
```

**Strict Verification Rule**: Empty tool output or an error is INDETERMINATE —
never report it as success. If a command returns empty, say you cannot verify.
Never invent pytest/grep/file results. The actual pytest stdout is the only
acceptable evidence of pass/fail.

## Related

- [[Verify-Backend-Change]]
- [[Codebase-Map]]
- [[Methodical-Process-Directive]]