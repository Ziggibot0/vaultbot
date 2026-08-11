---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-05
description: "Fix a broken or failing procedure by composing existing meta-procedures as diagnostic sub-tasks. Given a procedure name, it runs Analyze-Failure-Log to find root causes, Check-Tool-Coverage to find missing allowed_tools, Procedure-Coverage-Check to find step-description gaps, and Check-Procedure-Drift to detect intent drift. Then synthesizes a diagnosis, applies a patch (frontmatter and/or steps), and runs Procedure-Eval to verify the fix. Use when a procedure has a high failure rate, when Sean says 'fix this procedure', or after identifying a weak procedure."
when_to_use: "when a procedure is failing, when a procedure has >50% failure rate, when asked to fix or repair a procedure, when Post-Copilot-Audit or Procedure-Eval flags a broken procedure, when a procedure step produces wrong output but doesn't throw an error, when a procedure's behavior doesn't match its spec, when debugging why a procedure step isn't working as expected, when a procedure runs without errors but gives incorrect results, when manually troubleshooting a procedure and you need a systematic diagnosis"
falsifiable_if: "the fix it applies doesn't reduce the procedure's failure rate on re-run, or it diagnoses the wrong root cause (verifiable by checking the failure log)"
applies_to:
  - procedure-repair
  - procedure-maintenance
  - failure-recovery
  - procedure-improvement
allowed_tools:
  - code_run
  - vault_read_note
  - vault_safe_write
  - vault_lint
  - llm_generate
  - execute_procedure
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

## Inputs

- `procedure_name`: The wikilink title of the broken procedure (e.g. `Post-Copilot-Audit`)

## Steps

1. ```python
   import json

   # procedure_name is provided by the caller
   # We read it via vault_read_note — the framework injects the title
   # This step just confirms the procedure exists and captures its frontmatter

   result = {
       "procedure_name": procedure_name,
       "status": "read_complete",
       "note": f"Read {procedure_name} — frontmatter and steps captured for diagnosis."
   }
   ```

2. Call `run_procedure('Analyze-Failure-Log')` to extract failure patterns from the procedure's execution log. This gives us the root-cause hypothesis. The Analyze-Failure-Log procedure reads the execution tracker, finds all runs of the target procedure, and identifies what went wrong (timeouts, missing tools, step errors, empty output). **Output:** A structured failure report with timestamps, failure reasons, and the most common failure mode.

3. Call `run_procedure('Check-Tool-Coverage')` to verify that every tool used in the procedure's code steps is listed in `allowed_tools`. This catches the #1 failure mode seen in Post-Copilot-Audit (missing `code_run`). **Output:** A list of tools used in steps but missing from `allowed_tools`.

4. Call `run_procedure('Procedure-Coverage-Check')` to verify that the procedure's steps cover all tasks described in its `description` and `when_to_use` fields. This catches structural gaps where the procedure claims to do something but has no step for it. **Output:** A list of uncovered tasks — things the description promises but no step delivers.

5. Call `run_procedure('Check-Procedure-Drift')` to detect whether the procedure's steps have drifted from its original intent. This catches cases where someone edited steps without updating the description. **Output:** A drift report showing mismatches between stated intent and actual steps.

6. [llm: You are diagnosing a broken procedure. Here is the FAILURE LOG analysis only:

   ## Failure Log Analysis (Step 2)
   {step_2_output}

   Based ONLY on this failure log, list fixes needed. For each fix:
   1. What to change (frontmatter field, step content, or both)
   2. Why it's needed (which failure pattern found it)
   3. The exact replacement text (if frontmatter) or step instruction (if step)
   4. Priority: critical / important / nice-to-have

   Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet.]

7. [llm: You are diagnosing a broken procedure. Here is the TOOL COVERAGE check only:

   ## Tool Coverage Check (Step 3)
   {step_3_output}

   Based ONLY on this tool coverage report, list fixes needed. For each fix:
   1. What to change (frontmatter field, step content, or both)
   2. Why it's needed (which missing tool found it)
   3. The exact replacement text (if frontmatter) or step instruction (if step)
   4. Priority: critical / important / nice-to-have

   Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet.]

8. [llm: You are diagnosing a broken procedure. Here is the STEP COVERAGE check only:

   ## Step Coverage Check (Step 4)
   {step_4_output}

   Based ONLY on this step coverage report, list fixes needed. For each fix:
   1. What to change (frontmatter field, step content, or both)
   2. Why it's needed (which uncovered task found it)
   3. The exact replacement text (if frontmatter) or step instruction (if step)
   4. Priority: critical / important / nice-to-have

   Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet.]

9. [llm: You are diagnosing a broken procedure. Here is the DRIFT check only:

   ## Drift Check (Step 5)
   {step_5_output}

   Based ONLY on this drift report, list fixes needed. For each fix:
   1. What to change (frontmatter field, step content, or both)
   2. Why it's needed (which drift mismatch found it)
   3. The exact replacement text (if frontmatter) or step instruction (if step)
   4. Priority: critical / important / nice-to-have

   Output as a JSON array of fix objects with keys: what, why, exact_text, priority. Do not apply fixes yet.]

10. ```python
    import json

    # Merge the 4 per-diagnostic fix lists from steps 6-9
    # Each is available as: step_6_fixes, step_7_fixes, step_8_fixes, step_9_fixes
    # Deduplicate by "what" field, keep highest priority

    all_fixes = []
    for fix_list in [step_6_fixes, step_7_fixes, step_8_fixes, step_9_fixes]:
        if fix_list:
            all_fixes.extend(fix_list)

    # Deduplicate: if two fixes target the same "what", keep the one with higher priority
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
    fix_list = deduped

    result = {
        "total_fixes": len(fix_list),
        "critical": sum(1 for f in fix_list if f.get("priority") == "critical"),
        "important": sum(1 for f in fix_list if f.get("priority") == "important"),
        "nice_to_have": sum(1 for f in fix_list if f.get("priority") == "nice-to-have"),
        "fixes": fix_list
    }
    ```

11. ```python
    import json

    # The merged fix list from step 10 is available as `fix_list`
    # We apply fixes in priority order: critical first, then important
    # Nice-to-have fixes are skipped (deferred to next cycle)

    applied = []
    skipped = []

    for fix in fix_list:
        if fix["priority"] == "critical" or fix["priority"] == "important":
            applied.append({
                "what": fix["what"],
                "why": fix["why"],
                "priority": fix["priority"]
            })
        else:
            skipped.append({
                "what": fix["what"],
                "reason": "nice-to-have, deferred"
            })

    result = {
        "applied_fixes": applied,
        "skipped_fixes": skipped,
        "total_applied": len(applied),
        "total_skipped": len(skipped)
    }
    ```

    **Safety:** The actual write happens via `vault_safe_write` which backs up the original to trash/ before overwriting. If the write is blocked (LOCKED note), report it and stop — do not force.

12. Run `vault_lint` on the patched procedure to verify no broken wikilinks were introduced and frontmatter is valid.

13. Call `run_procedure('Procedure-Eval')` on the patched procedure. This runs the procedure and grades it on completeness, correctness, and efficiency. **Success criteria:** The procedure executes without step errors, no "missing tool" failures, and the LLM grade is >= 3/5 on the Procedure-Eval rubric. If the procedure still fails, loop back to step 6 with the new failure data (max 2 retry loops).

14. ```python
    import json

    # Summarize the full fix cycle
    result = {
        "procedure_name": procedure_name,
        "fixes_applied": len(applied),
        "fixes_skipped": len(skipped),
        "verification": "passed" if verification_passed else "failed",
        "retry_count": retry_count,
        "status": "fixed" if verification_passed else "needs_manual_review"
    }
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
| 13 | [[Procedure-Eval]] | Post-fix verification and grading |

What this procedure adds (the glue):
1. **Orchestration** — chains diagnostics in the right order
2. **Synthesis** — combines diagnostic outputs into a prioritized fix list (split across 4 small LLM calls to avoid timeout on the 0.8B model)
3. **Patch application** — applies frontmatter and step fixes via safe writes
4. **Verification loop** — re-runs eval after patching, retries if still broken

## Falsifiability

This procedure is falsifiable if:
- The fix it applies doesn't reduce the target procedure's failure rate on
  re-run (checkable via Analyze-Failure-Log after the fix)
- It diagnoses the wrong root cause (verifiable by comparing the diagnosis to
  the actual failure log entries)
- It breaks a previously-working procedure by applying an incorrect patch
  (verifiable via Procedure-Eval before and after)