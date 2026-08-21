---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Fix a broken or failing procedure by composing existing meta-procedures as diagnostic sub-tasks. Given a procedure name, it runs Analyze-Failure-Log to find root causes, Check-Tool-Coverage to find missing allowed_tools, Procedure-Coverage-Check to find step-description gaps, and Check-Procedure-Drift to detect intent drift. Then synthesizes a diagnosis, applies a patch (frontmatter and/or steps), and runs Procedure-Eval to verify the fix. Use when a procedure has a high failure rate, when the user says 'fix this procedure', or after identifying a weak procedure."
when_to_use: "when a procedure is failing, when a procedure has >50% failure rate, when asked to fix or repair a procedure, when Post-Copilot-Audit or Procedure-Eval flags a broken procedure, when a procedure step produces wrong output but doesn't throw an error, when a procedure's behavior doesn't match its spec, when debugging why a procedure step isn't working as expected, when a procedure runs without errors but gives incorrect results, when manually troubleshooting a procedure and you need a systematic diagnosis"
falsifiable_if: "the fix it applies doesn't reduce the procedure's failure rate on re-run, or it diagnoses the wrong root cause (verifiable by checking the failure log)"
applies_to:
  - procedure-repair
  - procedure-maintenance
  - failure-recovery
  - procedure-improvement
allowed_tools:
  - vault_safe_write
  - vault_lint
  - llm_generate
  - run_procedure
provides:
  - Analyze-Failure-Log
  - Check-Tool-Coverage
  - Procedure-Coverage-Check
  - Check-Procedure-Drift
  - Procedure-Eval
summary: Procedure-Fixer
tags:
  - procedure
  - procedures
  - meta-procedure
---

# Procedure-Fixer

## When to Run This

When a procedure has a high failure rate, has never succeeded, or has been
flagged as broken by [[Procedure-Eval]] or [[Post-Copilot-Audit]]. This
procedure **delegates** to existing meta-procedures for diagnosis rather than
duplicating their logic. It only adds the glue: orchestration, patch
application, and verification.

## Why This Exists

A broken procedure needs diagnosis across multiple dimensions (failure log, tool coverage, step coverage, drift), but duplicating each diagnostic's logic would bloat the library. This procedure closes that gap by composing existing meta-procedures as diagnostic sub-tasks and only adding the glue: orchestration, patch application, and verification. The tradeoff is that it delegates rather than reimplements, so it depends on those sub-procedures being correct.

## Inputs

- `procedure_name`: The wikilink title of the broken procedure (e.g. `Post-Copilot-Audit`)

## Steps

### Step 1: Read the target procedure name from args

```python
import json

procedure_name = args.get('procedure_name', '')
if not procedure_name:
    result = json.dumps({"error": "procedure_name argument required"})
else:
    result = json.dumps({
        "procedure_name": procedure_name,
        "status": "read_complete",
        "note": f"Target: {procedure_name} — ready for diagnosis."
    })
```

### Step 2: Run Analyze-Failure-Log diagnostic

```python
import json

step1 = json.loads(output)
if step1.get("error"):
    result = output
else:
    target = step1.get("procedure_name", "")
    try:
        diag_result = run_procedure("Analyze-Failure-Log", {"procedure_name": target})
        if isinstance(diag_result, dict):
            diag_text = diag_result.get("final_output", json.dumps(diag_result))
        else:
            diag_text = str(diag_result)
    except Exception as e:
        diag_text = f"ERROR running Analyze-Failure-Log: {e}"
    result = json.dumps({
        "procedure_name": target,
        "diagnostic": "Analyze-Failure-Log",
        "output": diag_text[:3000]
    })
```

### Step 3: Run Check-Tool-Coverage diagnostic

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return "{}"

step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")
if not target:
    result = json.dumps({"error": "no procedure_name from step 1"})
else:
    try:
        diag_result = run_procedure("Check-Tool-Coverage", {"procedure_name": target})
        if isinstance(diag_result, dict):
            diag_text = diag_result.get("final_output", json.dumps(diag_result))
        else:
            diag_text = str(diag_result)
    except Exception as e:
        diag_text = f"ERROR running Check-Tool-Coverage: {e}"
    result = json.dumps({
        "procedure_name": target,
        "diagnostic": "Check-Tool-Coverage",
        "output": diag_text[:3000]
    })
```

### Step 4: Run Procedure-Coverage-Check diagnostic

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return "{}"

step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")
if not target:
    result = json.dumps({"error": "no procedure_name from step 1"})
else:
    try:
        diag_result = run_procedure("Procedure-Coverage-Check", {"procedure_name": target})
        if isinstance(diag_result, dict):
            diag_text = diag_result.get("final_output", json.dumps(diag_result))
        else:
            diag_text = str(diag_result)
    except Exception as e:
        diag_text = f"ERROR running Procedure-Coverage-Check: {e}"
    result = json.dumps({
        "procedure_name": target,
        "diagnostic": "Procedure-Coverage-Check",
        "output": diag_text[:3000]
    })
```

### Step 5: Run Check-Procedure-Drift diagnostic

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return "{}"

step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")
if not target:
    result = json.dumps({"error": "no procedure_name from step 1"})
else:
    try:
        diag_result = run_procedure("Check-Procedure-Drift", {"procedure_name": target})
        if isinstance(diag_result, dict):
            diag_text = diag_result.get("final_output", json.dumps(diag_result))
        else:
            diag_text = str(diag_result)
    except Exception as e:
        diag_text = f"ERROR running Check-Procedure-Drift: {e}"
    result = json.dumps({
        "procedure_name": target,
        "diagnostic": "Check-Procedure-Drift",
        "output": diag_text[:3000]
    })
```

### Step 6: Diagnose fixes from failure log analysis

[llm: You are diagnosing a broken procedure. Based ONLY on the Step 2 output above (the Analyze-Failure-Log diagnostic), list fixes needed. For each fix:
1. What to change (frontmatter field, step content, or both)
2. Why it's needed (which failure pattern found it)
3. The exact replacement text (if frontmatter) or step instruction (if step)
4. Priority: critical / important / nice-to-have

Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet. Return ONLY the JSON array.]

### Step 7: Diagnose fixes from tool coverage check

[llm: You are diagnosing a broken procedure. Based ONLY on the Step 3 output above (the Check-Tool-Coverage diagnostic), list fixes needed. For each fix:
1. What to change (frontmatter field, step content, or both)
2. Why it's needed (which missing tool found it)
3. The exact replacement text (if frontmatter) or step instruction (if step)
4. Priority: critical / important / nice-to-have

Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet. Return ONLY the JSON array.]

### Step 8: Diagnose fixes from step coverage check

[llm: You are diagnosing a broken procedure. Based ONLY on the Step 4 output above (the Procedure-Coverage-Check diagnostic), list fixes needed. For each fix:
1. What to change (frontmatter field, step content, or both)
2. Why it's needed (which uncovered task found it)
3. The exact replacement text (if frontmatter) or step instruction (if step)
4. Priority: critical / important / nice-to-have

Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet. Return ONLY the JSON array.]

### Step 9: Diagnose fixes from drift check

[llm: You are diagnosing a broken procedure. Based ONLY on the Step 5 output above (the Check-Procedure-Drift diagnostic), list fixes needed. For each fix:
1. What to change (frontmatter field, step content, or both)
2. Why it's needed (which drift mismatch found it)
3. The exact replacement text (if frontmatter) or step instruction (if step)
4. Priority: critical / important / nice-to-have

Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet. Return ONLY the JSON array.]

### Step 10: Merge and deduplicate fixes from all four diagnoses

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return ""

def parse_fix_list(text):
    """Parse a JSON array of fix objects from LLM output text."""
    text = text.strip()
    # Find the JSON array in the text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass
    return []

# Collect fix lists from steps 6-9 (LLM steps)
all_fixes = []
for step_num in [6, 7, 8, 9]:
    llm_output = get_prior(prior_results, step_num)
    fixes = parse_fix_list(llm_output)
    all_fixes.extend(fixes)

# Deduplicate by "what" field, keep highest priority
priority_order = {"critical": 0, "important": 1, "nice-to-have": 2}
seen = {}
for fix in all_fixes:
    key = fix.get("what", "")
    if key not in seen:
        seen[key] = fix
    else:
        if priority_order.get(fix.get("priority", "nice-to-have"), 2) < \
           priority_order.get(seen[key].get("priority", "nice-to-have"), 2):
            seen[key] = fix

# Sort by priority: critical first, then important, then nice-to-have
deduped = sorted(seen.values(), key=lambda f: priority_order.get(f.get("priority", "nice-to-have"), 2))

result = json.dumps({
    "total_fixes": len(deduped),
    "critical": sum(1 for f in deduped if f.get("priority") == "critical"),
    "important": sum(1 for f in deduped if f.get("priority") == "important"),
    "nice_to_have": sum(1 for f in deduped if f.get("priority") == "nice-to-have"),
    "fixes": deduped
})
```

### Step 11: Apply critical and important fixes via LLM-generated patch

```python
import json, os
from pathlib import Path

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return ""

# Parse the merged fix list from step 10
step10_output = get_prior(prior_results, 10)
try:
    step10 = json.loads(step10_output)
except json.JSONDecodeError:
    step10 = {"fixes": [], "total_fixes": 0}

fix_list = step10.get("fixes", [])

# Get the target procedure name
step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")

# Separate critical/important from nice-to-have
applied = []
skipped = []
for fix in fix_list:
    if fix.get("priority") in ("critical", "important"):
        applied.append(fix)
    else:
        skipped.append({"what": fix.get("what", ""), "reason": "nice-to-have, deferred"})

# Read the current procedure file
proc_path = Path(vault_path) / "vaultbot" / "System" / "Procedures" / f"{target}.md"
current_content = ""
if proc_path.exists():
    current_content = proc_path.read_text(encoding="utf-8", errors="replace")

# If there are fixes to apply, use llm_generate to produce patched content
patched_content = ""
if applied and current_content:
    fix_descriptions = json.dumps(applied, indent=2)
    patch_prompt = f"""You are a procedure repair system. Given a procedure note and a list of fixes, produce the FULL patched procedure markdown.

Current procedure:
<<<
{current_content[:8000]}
>>>

Fixes to apply:
{fix_descriptions}

Rules:
- Apply ONLY the critical and important fixes listed above
- Do NOT change anything else
- Preserve the YAML frontmatter structure
- Preserve all step headers and code blocks
- Return the FULL patched markdown, including frontmatter
- Return ONLY the markdown, no commentary"""

    patched_content = llm_generate(patch_prompt)

    # Write the patched content
    if patched_content and len(patched_content) > 50:
        write_result = vault_safe_write(
            f"vaultbot/System/Procedures/{target}.md",
            patched_content
        )
        write_status = "written"
    else:
        write_status = "patch_too_short"
else:
    write_status = "no_fixes_to_apply" if not applied else "no_original_content"

result = json.dumps({
    "target": target,
    "fixes_applied": len(applied),
    "fixes_skipped": len(skipped),
    "applied_fixes": [{"what": f.get("what", ""), "why": f.get("why", ""), "priority": f.get("priority", "")} for f in applied],
    "skipped_fixes": skipped,
    "write_status": write_status
})
```

### Step 12: Lint the patched procedure

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return ""

step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")
file_path = f"vaultbot/System/Procedures/{target}.md"

lint_result = vault_lint(file_path)

result = json.dumps({
    "target": target,
    "lint_issues": lint_result.get("issues", []) if isinstance(lint_result, dict) else [],
    "broken_links": lint_result.get("broken_wikilinks", []) if isinstance(lint_result, dict) else []
})
```

### Step 13: Run Procedure-Eval to verify the fix

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return ""

step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")

try:
    eval_result = run_procedure("Procedure-Eval", {"procedure_name": target})
    if isinstance(eval_result, dict):
        eval_passed = eval_result.get("overall_passed", False)
        eval_output = eval_result.get("final_output", json.dumps(eval_result))[:2000]
    else:
        eval_passed = False
        eval_output = str(eval_result)[:2000]
except Exception as e:
    eval_passed = False
    eval_output = f"ERROR running Procedure-Eval: {e}"

result = json.dumps({
    "target": target,
    "verification_passed": eval_passed,
    "eval_output": eval_output
})
```

### Step 14: Final report

```python
import json

def get_prior(prior_results, step_num):
    for key in [str(step_num), str(float(step_num))]:
        if key in prior_results:
            return prior_results[key]
    return ""

step1 = json.loads(get_prior(prior_results, 1))
target = step1.get("procedure_name", "")

step10 = json.loads(get_prior(prior_results, 10)) if get_prior(prior_results, 10) else {}
step11 = json.loads(get_prior(prior_results, 11)) if get_prior(prior_results, 11) else {}
step13 = json.loads(get_prior(prior_results, 13)) if get_prior(prior_results, 13) else {}

fixes_applied = step11.get("fixes_applied", 0)
fixes_skipped = step11.get("fixes_skipped", 0)
verification_passed = step13.get("verification_passed", False)

result = json.dumps({
    "procedure_name": target,
    "total_fixes_identified": step10.get("total_fixes", 0),
    "critical_fixes": step10.get("critical", 0),
    "important_fixes": step10.get("important", 0),
    "fixes_applied": fixes_applied,
    "fixes_skipped": fixes_skipped,
    "verification": "passed" if verification_passed else "failed",
    "status": "fixed" if verification_passed else "needs_manual_review",
    "report": f"Procedure {target}: {fixes_applied} fixes applied, {fixes_skipped} deferred. Verification: {'passed' if verification_passed else 'failed'}."
})
```

## Composition Map

This procedure composes existing meta-procedures rather than duplicating their
logic:

| Step | Delegates to | Purpose |
|------|-------------|---------|
| 2 | [[Analyze-Failure-Log]] | Root-cause analysis from execution logs |
| 3 | [[Check-Tool-Coverage]] | Missing allowed_tools detection |
| 4 | [[Procedure-Coverage-Check]] | Step-description coverage gaps |
| 5 | [[Check-Procedure-Drift]] | Intent-vs-implementation drift |
| 6-9 | (4 small LLM calls) | Per-diagnostic fix extraction — one diagnosis per call |
| 10 | (Python merge) | Deduplicate and prioritize fixes from all 4 calls |
| 11 | (llm_generate + vault_safe_write) | Generate patched content and write to disk |
| 12 | vault_lint | Verify no broken wikilinks introduced |
| 13 | [[Procedure-Eval]] | Post-fix verification and grading |

What this procedure adds (the glue):
1. **Orchestration** — chains diagnostics in the right order
2. **Synthesis** — combines diagnostic outputs into a prioritized fix list (split across 4 small LLM calls to avoid timeout on the 0.8B model)
3. **Patch application** — uses llm_generate to produce patched content, writes via vault_safe_write
4. **Verification loop** — re-runs eval after patching

## Falsifiability

This procedure is falsifiable if:
- The fix it applies doesn't reduce the target procedure's failure rate on
  re-run (checkable via Analyze-Failure-Log after the fix)
- It diagnoses the wrong root cause (verifiable by comparing the diagnosis to
  the actual failure log entries)
- It breaks a previously-working procedure by applying an incorrect patch
  (verifiable via Procedure-Eval before and after)

## Related

- [[Procedure-Eval]] — flags broken procedures and verifies the fix
- [[Analyze-Failure-Log]] — the root-cause diagnostic this procedure delegates to
- [[Check-Tool-Coverage]] — the missing-tool diagnostic this procedure delegates to