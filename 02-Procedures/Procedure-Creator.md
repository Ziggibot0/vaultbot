---
type: procedure
status: experimental
created: 2026-07-28
last_reviewed: 2026-07-28
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
description: "Meta-procedure for creating new procedures: reads a draft from _procedure_draft.md, runs static validation + dry-run sandbox test, publishes only if all checks pass. Catches the 7 friction points from Dream-Pass creation."
falsifiable_if: "a procedure published by following these steps fails on first live execution"
applies_to:
  - procedure-creation
  - self-improvement
  - validation
depends_on:
  - "[[Procedural-Bootstrap-and-Evolution-Plan]]"
  - "[[Dream-Pass]]"
sources:
  - "https://arxiv.org/abs/2604.20496v1"
  - "https://arxiv.org/abs/2605.25665v1"
allowed_tools:
  - vault_lint
---

# Procedure-Creator

## When to Use This

Use this procedure when you need to create a new procedure note for a recurring task. This meta-procedure sandboxes and validates the draft before publishing, catching the 7 friction points discovered during Dream-Pass creation:

1. **Format mismatch** — `### Step N:` headers without `N. ```python` steps
2. **Tool API mismatch** — `run_tool()` instead of direct tool names
3. **Missing tool injections** — tool called in code but not in `allowed_tools`
4. **Idempotency not designed in** — no link_exists/dedup patterns
5. **Quality not validated** — non-deterministic validation predicates
6. **Syntax errors** — code steps that don't compile
7. **LLM endpoint coupling** — direct socket/localhost instead of `get_llm_client()`

## Before Running This

1. Research how experts do the task (use `vault_research`)
2. Draft the procedure as a markdown note with proper frontmatter
3. Write the draft to `_procedure_draft.md` in the vault root
4. Then call `execute_procedure("How-to-Create-a-Procedure")`

The draft must have:
- `type: procedure` in frontmatter
- `description` (one-line summary)
- `allowed_tools` (list of tool names)
- `falsifiable_if` (condition that would prove it wrong)
- `## Steps` section with `N. ```python` or `N. [llm:]` steps

## Steps

### Step 0: Read Draft

Read the draft procedure from `_procedure_draft.md`.

0. ```python
import os, json

vault_path = os.environ.get("VAULT_PATH", ".")
draft_path = os.path.join(vault_path, "_procedure_draft.md")

if not os.path.exists(draft_path):
    raise RuntimeError("No draft found at _procedure_draft.md. Write your draft there first.")

with open(draft_path, encoding="utf-8") as f:
    draft_text = f.read()

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
import os, json

vault_path = os.environ.get("VAULT_PATH", ".")
draft_path = os.path.join(vault_path, "_procedure_draft.md")

with open(draft_path, encoding="utf-8") as f:
    draft_text = f.read()

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
import os, json

# Check Step 1 passed
_step1 = json.loads(prior_results[-1]) if prior_results else {}
if _step1.get("status") != "passed":
    raise RuntimeError(f"Step 1 did not pass: {_step1.get('status')}")

vault_path = os.environ.get("VAULT_PATH", ".")
draft_path = os.path.join(vault_path, "_procedure_draft.md")

with open(draft_path, encoding="utf-8") as f:
    draft_text = f.read()

from procedure_validator import dry_run_procedure

dry_result = dry_run_procedure(draft_text, vault_path, timeout=10)

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

Write the validated draft to the vault as a proper procedure note. Extract the title from the first `#` heading.

3. ```python
import os, json, re

# Check Steps 1 and 2 passed
_step1 = json.loads(prior_results[1]) if len(prior_results) > 1 else {}
_step2 = json.loads(prior_results[-1]) if prior_results else {}
if _step1.get("status") != "passed" or _step2.get("status") != "passed":
    raise RuntimeError("Cannot publish: validation or dry run did not pass")

vault_path = os.environ.get("VAULT_PATH", ".")
draft_path = os.path.join(vault_path, "_procedure_draft.md")

with open(draft_path, encoding="utf-8") as f:
    draft_text = f.read()

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

# Don't overwrite existing notes
proc_path = os.path.join(vault_path, proc_filename)
if os.path.exists(proc_path):
    raise RuntimeError(f"Note already exists: {proc_filename}. Rename or remove it first.")

# Write the published procedure
with open(proc_path, "w", encoding="utf-8") as f:
    f.write(draft_text)

# Clean up the draft file
os.remove(draft_path)

result = json.dumps({
    "status": "published",
    "filename": proc_filename,
    "title": proc_title,
    "path": proc_path,
})
```

### Step 4: Verify

Lint the published procedure to confirm it has no broken wikilinks or quality issues.

4. ```python
import os, json

# Check Step 3 passed
_step3 = json.loads(prior_results[-1]) if prior_results else {}
if _step3.get("status") != "published":
    raise RuntimeError(f"Step 3 did not publish: {_step3.get('status')}")

filename = _step3.get("filename", "")
vault_path = os.environ.get("VAULT_PATH", ".")
proc_path = os.path.join(vault_path, filename)

if not os.path.exists(proc_path):
    raise RuntimeError(f"Published file not found: {filename}")

lint_result = vault_lint(proc_path)

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

reports = []
for i, r in enumerate(prior_results):
    try:
        parsed = json.loads(r) if isinstance(r, str) else r
        reports.append({"step": i, "result": parsed})
    except:
        reports.append({"step": i, "raw": str(r)[:200]})

# Count warnings from Step 1
_step1 = json.loads(prior_results[1]) if len(prior_results) > 1 else {}
warnings_count = len(_step1.get("warnings", []))

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
- Step 3 publishes the procedure to the vault
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

## Research Basis

- **Pre-deployment verification** (arXiv 2604.20496): Formal verification before deployment catches issues that behavioral testing misses. The four-layer containment framework (pre-deployment verification, pre-execution constraints, output control, runtime monitoring) maps to our static validation, dry run, publish gate, and live test.
- **Contract-driven adversarial verification** (arXiv 2605.25665): Treat procedure creation as a governed pipeline with contracts (description, falsifiable_if), not free-form writing. Early deployment reports catch contract incompleteness.
- **Digital twin for procedure verification** (IEEE 10575381): Use a sandbox (digital twin) to verify procedures before live execution. Our dry-run step is the digital twin.
- **TDD for V&V&I** (arXiv cs/0609119): Adapt test-driven development for verification, validation, and integrity testing of procedures. Write validation criteria first, then verify the procedure meets them.
- **Idempotency by design** (Zero Trust Marketing): Design handlers to be idempotent from the start, not as an afterthought.

## Related

- [[Dream-Pass]] — the procedure whose creation friction inspired this meta-procedure
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the framework this lives in
- [[Procedure-Subprocess-Architecture]] — how procedures execute
- [[How-to-Write-a-Python-Tool]] — companion procedure for tool creation
- [[Exemplar-Tool-Creation]] — exemplar for the tool creation process
