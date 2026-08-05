---
type: procedure
status: active
model_cartridge: big
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
---

# JS-Safe-Write

Edit JavaScript files safely. Validates JS syntax with node --check before writing to disk (atomic write pattern: write to temp, validate, swap). If syntax validation fails, the real file is never touched.

## Steps

1. [llm: Read the JS file you want to edit using code_read to understand its current state.]

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

3. [llm: If dry_run passed, re-run with dry_run=False to apply. If rejected, fix the JS syntax error before retrying.]