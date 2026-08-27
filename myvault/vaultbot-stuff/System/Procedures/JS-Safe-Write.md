---
type: procedure
status: active
baseline: true
created: 2026-07-31
description: Safely edit JavaScript files with node syntax validation before writing.
when_to_use: When you need to edit the Obsidian plugin main.js or any .js/.mjs/.cjs file.
allowed_tools:
  - js_safe_write
  - code_read
summary: JS-Safe-Write
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# JS-Safe-Write

Edit JavaScript files safely. Validates JS syntax with node --check before writing to disk (atomic write pattern: write to temp, validate, swap). If syntax validation fails, the real file is never touched.

## Why This Exists

Editing the Obsidian plugin's `main.js` (or any `.js`/`.mjs`/`.cjs`) can
break the plugin if the syntax is wrong. This procedure validates JS syntax
with `node --check` before writing, using an atomic write pattern. The
tradeoff: it requires Node to be available, and only catches syntax errors,
not logic errors.

## Steps

### Step 1: Read the JS file to understand its current state

1. [llm: Read the JS file you want to edit using code_read to understand its current state.]

### Step 2: Write the JS file with syntax validation

2. ```python
   from self_improver import SelfImprover
   _si = SelfImprover(session_logger=None)
   result = _si.js_safe_write(
       file_path=args["file_path"],
       content=args["content"],
       dry_run=args.get("dry_run", False)
   )
   print(result)
   ```

### Step 3: Apply the edit or fix the syntax error

3. [llm: If dry_run passed, re-run with dry_run=False to apply. If rejected, fix the JS syntax error before retrying.]

## Related

- [[Safe-Write]] — the Python equivalent (import-graph verified, auto-rollback)
- [[Choose-Write-Tool]] — decides which write tool to use for a given file
- [[Verify-Syntax]] — syntax verification for backend changes