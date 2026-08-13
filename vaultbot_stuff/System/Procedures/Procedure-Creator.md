---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-07-28
restored: 2026-08-09
description: "Meta-procedure for creating new procedures: reads a draft from _procedure_draft.md, runs static validation + dry-run sandbox test, publishes only if all checks pass. Catches the 7 friction points from Dream-Pass creation."
when_to_use: when you need to create a new procedure from a draft, when a draft is ready at _procedure_draft.md, when you want sandboxed validation before publishing, or when asked to publish a procedure draft
falsifiable_if: "a procedure published by following these steps fails on first live execution"
applies_to:
  - procedure-creation
  - self-improvement
  - validation
allowed_tools:
  - vault_lint
  - vault_safe_write
summary: |
  Procedure-Creator is a meta-procedure that sandboxes and validates new procedure drafts before publishing.
  1. Read draft from _procedure_draft.md
  2. Run 13 static validation checks via procedure_validator
  3. Dry-run all code steps in a sandbox
  4. Publish to vaultbot_stuff/System/Procedures/ only if all checks pass
  5. Lint the published procedure
tags:
  - procedure
  - meta-procedure
  - validation
  - self-improvement
---

# Procedure-Creator

## When to Use This

Use this procedure when you need to create a new procedure note for a recurring task. This meta-procedure sandboxes and validates the draft before publishing, catching the 7 friction points discovered during Dream-Pass creation:

1. **Format mismatch** — steps missing `### Step N: short-summary` headers (every step MUST have one)
2. **Tool API mismatch** — `run_tool()` instead of direct tool names
3. **Missing tool injections** — tool called in code but not in `allowed_tools`
4. **Idempotency not designed in** — no link_exists/dedup patterns
5. **Quality not validated** — non-deterministic validation predicates
6. **Syntax errors** — code steps that don't compile
7. **LLM endpoint coupling** — direct socket/localhost instead of `get_llm_client()`

## Before Running This

1. Research how experts do the task
2. Draft the procedure as a markdown note with proper frontmatter
3. Write the draft to `_procedure_draft.md` in the vault root
4. Then call `execute_procedure("Procedure-Creator")`

The draft must follow the **unified procedure format** (see [[Procedural-Bootstrap-and-Evolution-Plan]] Part 3 and [[Build-Procedure]]):

### Required Frontmatter
```yaml
---
type: procedure
status: experimental
model_cartridge: small  # or big, or vision
created: YYYY-MM-DD
description: "one-line summary for retrieval"
when_to_use: "SITUATIONS that trigger this procedure"
falsifiable_if: "specific, observable failure condition"
allowed_tools:
  - tool_name
summary: Short-Title
tags:
  - procedure
  - procedures
---
```

### Step Format
Every step MUST use this exact format:

```markdown
### Step N: Short human-readable summary

N. ```python
code here
```

### Step N: Short human-readable summary

N. [llm: instruction here]
```

- The `### Step N:` header provides the human-readable description.
- The `N.` prefix on the code fence or LLM tag makes step numbers visible in raw markdown.
- Code steps use ```python blocks. LLM steps use `[llm: ...]` tags.
- NEVER use bare `N.` without a `### Step N:` header above it.
- NEVER use `[vllm:]`, `[model_cartridge:]`, or any other tag format — only `[llm: ...]`.

### Standardized Sections (in this order)
1. `## When to Run This` — trigger conditions
2. `## Inputs` — documented args (if any)
3. `## Steps` — the machine-executable steps
4. `## Why This Exists` — the failure or gap that spawned this procedure
5. `## Related` — wikilinks to related notes

## Steps

### Step 0: Read Draft

Read the draft procedure from `_procedure_draft.md`.

0. ```python
import os, json
from pathlib import Path

vault_root = Path(vault_path)
draft_path = vault_root / "_procedure_draft.md"

if not draft_path.exists():
    raise RuntimeError("No draft found at _procedure_draft.md. Write your draft there first.")

draft_text = draft_path.read_text(encoding="utf-8")

if not draft_text.strip():
    raise RuntimeError("Draft file is empty")

if not draft_text.startswith("---"):
    raise RuntimeError("Draft must start with frontmatter (---)")

result = json.dumps({
    "status": "ok",
    "draft_length": len(draft_text),
    "draft_preview": draft_text[:200],
})
```

### Step 1: Static Validation

Run 13 deterministic checks on the draft text: frontmatter completeness, compile test, sequential numbering, tool consistency, anti-patterns, validation predicates, idempotency indicators, result variable, and syntax check.

1. ```python
import os, json, sys
from pathlib import Path

vault_root = Path(vault_path)
draft_path = vault_root / "_procedure_draft.md"

draft_text = draft_path.read_text(encoding="utf-8")

# Add vaultbot_backend to path for procedure_validator import
backend_dir = vault_root / "vaultbot_stuff" / "vaultbot_backend"
sys.path.insert(0, str(backend_dir))

from procedure_validator import validate_procedure_text

validation = validate_procedure_text(draft_text)

if not validation["passed"]:
    raise RuntimeError(
        f"Static validation FAILED with {len(validation['errors'])} errors: "
        + "; ".join(validation["errors"])
    )

result = json.dumps({
    "status": "passed",
    "errors": validation["errors"],
    "warnings": validation["warnings"],
    "checks_run": validation["checks_run"],
    "compiled_steps": validation["compiled_steps"],
    "step_types": validation["step_types"],
    "step_numbers": validation["step_numbers"],
    "allowed_tools": validation["allowed_tools"],
    "tool_calls_found": validation["tool_calls_found"],
})
```

### Step 2: Dry Run

Execute each code step in a sandbox with mocked tools and a 10-second timeout. No side effects on the vault.

2. ```python
import os, json, sys
from pathlib import Path

vault_root = Path(vault_path)
draft_path = vault_root / "_procedure_draft.md"

draft_text = draft_path.read_text(encoding="utf-8")

backend_dir = vault_root / "vaultbot_stuff" / "vaultbot_backend"
sys.path.insert(0, str(backend_dir))

from procedure_validator import dry_run_procedure

dry_result = dry_run_procedure(draft_text, str(vault_root), timeout=10)

if not dry_result["passed"]:
    failed_steps = [r for r in dry_result["results"] if r["status"] in ("error", "timeout")]
    raise RuntimeError(
        f"Dry run FAILED: {len(failed_steps)} step(s) failed: "
        + "; ".join(f"Step {r['step']}: {r.get('error', r.get('status', ''))}" for r in failed_steps)
    )

result = json.dumps({
    "status": "passed",
    "steps_tested": dry_result["steps_tested"],
    "results": dry_result["results"],
})
```

### Step 3: Publish

Write the validated draft to `vaultbot_stuff/System/Procedures/`. Extract the title from the first `#` heading.

3. ```python
import os, json, re
from pathlib import Path

vault_root = Path(vault_path)
draft_path = vault_root / "_procedure_draft.md"
proc_dir = vault_root / "vaultbot_stuff" / "System" / "Procedures"

draft_text = draft_path.read_text(encoding="utf-8")

# Extract title from first # heading
fm_end = draft_text.find("\n---", 3)
body = draft_text[fm_end + 4:].lstrip() if fm_end != -1 else draft_text
title_match = re.match(r'^#\s+(.+)$', body, re.MULTILINE)
if title_match:
    proc_title = title_match.group(1).strip()
    proc_filename = proc_title.replace(" ", "-") + ".md"
else:
    proc_filename = "Untitled-Procedure.md"
    proc_title = "Untitled Procedure"

# Write to vaultbot_stuff/System/Procedures/
proc_path = proc_dir / proc_filename

# Don't overwrite existing notes
if proc_path.exists():
    raise RuntimeError(f"Note already exists: {proc_filename}. Rename or remove it first.")

proc_path.write_text(draft_text, encoding="utf-8")

# Clean up the draft file
draft_path.unlink()

result = json.dumps({
    "status": "published",
    "filename": proc_filename,
    "title": proc_title,
    "path": str(proc_path),
})
```

### Step 4: Verify

Lint the published procedure to confirm it has no broken wikilinks or quality issues.

4. ```python
import os, json
from pathlib import Path

vault_root = Path(vault_path)
proc_dir = vault_root / "vaultbot_stuff" / "System" / "Procedures"

# Find the most recently created .md file in the procedures directory
# (more reliable than reading prior_results which may have key mismatches)
newest = None
newest_time = 0
for f in proc_dir.rglob("*.md"):
    mtime = f.stat().st_mtime
    if mtime > newest_time:
        newest_time = mtime
        newest = f

if newest is None:
    raise RuntimeError("No procedure files found in " + str(proc_dir))

filename = newest.name

# vault_lint is injected as a callable
lint_result = vault_lint(str(newest))

result = json.dumps({
    "status": "verified",
    "filename": filename,
    "lint": lint_result,
})
```

### Step 5: Report

Summarize all validation results in a single report.

5. ```python
import json

# Collect results from all prior steps
reports = {}
for key, val in prior_results.items():
    try:
        reports[key] = json.loads(val) if isinstance(val, str) else val
    except:
        reports[key] = str(val)[:200]

# Count warnings from Step 1
step1_data = reports.get("step_1", {})
warnings_count = len(step1_data.get("warnings", []))

result = json.dumps({
    "status": "complete",
    "steps_executed": len(reports),
    "warnings_count": warnings_count,
    "reports": reports,
})
```

## Validation Criteria

This procedure is working correctly when:
- Step 1 reports 0 errors (static validation passes)
- Step 2 reports all code steps passed (dry run succeeds)
- Step 3 publishes the procedure to `vaultbot_stuff/System/Procedures/`
- Step 4 verifies the published file exists and lints clean
- The published procedure runs successfully on first live execution

## What This Catches

| Friction Point | Check | Step |
|---|---|---|
| Format mismatch (`### Step N:`) | `compile_test` | 1 |
| Tool API mismatch (`run_tool()`) | `tool_calls_in_allowed_tools` | 1 |
| Missing tool injections | `tool_calls_in_allowed_tools` | 1 |
| Idempotency not designed in | `idempotency_indicators` | 1 (warning) |
| Quality not validated | `validation_predicates` | 1 (warning) |
| Syntax errors | `syntax_check` | 1 |
| LLM endpoint coupling | `no_direct_endpoints` | 1 (warning) |
| Runtime crashes | `dry_run_procedure` | 2 |
| Missing result variable | `result_variable` | 1 (warning) |

## Related

- [[Dream-Pass]] — the procedure whose creation friction inspired this meta-procedure
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the framework this lives in
- [[Procedure-Subprocess-Architecture]] — how procedures execute
