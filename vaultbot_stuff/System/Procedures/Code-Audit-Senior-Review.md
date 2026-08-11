---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-05
description: "Orchestrating code audit that calls granular check procedures (Check-Error-Handling, Check-Resource-Leaks, Check-Mutable-Defaults, Check-Complexity, Check-Dead-Code) via run_procedure(), aggregates findings, and produces a senior-level review summary with prioritized recommendations."
when_to_use: when performing a full code audit on a Python file, before a commit, when asked 'review this code', or when you need a comprehensive quality assessment
falsifiable_if: the orchestrator reports violations that the granular procedures don't find, or misses violations they do find
applies_to:
  - code-quality
  - code-audit
  - orchestration
allowed_tools:
  - code_read
  - llm_generate
  - run_procedure
provides:
  - Check-Error-Handling
  - Check-Resource-Leaks
  - Check-Mutable-Defaults
  - Check-Complexity
  - Check-Dead-Code
summary: Code-Audit-Senior-Review
tags:
  - procedure
  - procedures
  - code-audit
  - orchestration
---

# Code-Audit-Senior-Review

## When to Run This

Run this to perform a comprehensive code audit on a Python file.
It calls five granular check procedures via `run_procedure()`,
aggregates their findings, and produces a senior-level review
summary with prioritized recommendations.

**Granular procedures called:**
- [[Check-Error-Handling]] — bare excepts, silent swallowing, broad catches
- [[Check-Resource-Leaks]] — unclosed files/connections without `with` blocks
- [[Check-Mutable-Defaults]] — mutable default arguments (`[]`, `{}`, `{}`)
- [[Check-Complexity]] — function length >50 lines, nesting depth >4
- [[Check-Dead-Code]] — unused imports, unreachable branches

## Steps

### Step 1: Run all five granular check procedures on the target file

1. ```python
import json

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    # Call each granular procedure and capture its output
    checks = [
        "Check-Error-Handling",
        "Check-Resource-Leaks",
        "Check-Mutable-Defaults",
        "Check-Complexity",
        "Check-Dead-Code",
    ]
    results = {}
    for proc_name in checks:
        try:
            proc_result = run_procedure(proc_name, {"file_path": file_path})
            results[proc_name] = proc_result
        except Exception as e:
            results[proc_name] = json.dumps({"error": f"procedure {proc_name} failed: {e}"})

    result = json.dumps({
        "file_path": file_path,
        "checks_run": checks,
        "raw_results": results,
        "status": "all_checks_complete"
    })
    ```

### Step 2: Parse and aggregate all findings into a unified violation list

2. ```python
import json

# Parse the raw results from step 1
raw = json.loads(output)
file_path = raw.get("file_path", "unknown")
all_results = raw.get("raw_results", {})

all_violations = []
check_summaries = []

for proc_name, proc_output in all_results.items():
    try:
        if isinstance(proc_output, str):
            proc_data = json.loads(proc_output)
        else:
            proc_data = proc_output

        # run_procedure() returns a wrapper dict with "final_output"
        # containing the actual check result as a JSON string.
        # Unwrap it to get the real check data.
        if isinstance(proc_data, dict) and "final_output" in proc_data:
            final = proc_data["final_output"]
            if isinstance(final, str):
                proc_data = json.loads(final)
            elif isinstance(final, dict):
                proc_data = final
    except (json.JSONDecodeError, TypeError):
        check_summaries.append({
            "procedure": proc_name,
            "status": "parse_error",
            "violations": 0
        })
        continue

    # Extract violations from the procedure's output
    # Each granular procedure returns a JSON object with a "violations" or "findings" key
    violations = proc_data.get("violations", proc_data.get("findings", []))
    if isinstance(violations, list):
        for v in violations:
            v["source_check"] = proc_name
            all_violations.append(v)

    check_summaries.append({
        "procedure": proc_name,
        "status": proc_data.get("status", "unknown"),
        "violation_count": len(violations) if isinstance(violations, list) else 0,
        "error": proc_data.get("error")
    })

# Sort violations by severity (critical > high > medium > low)
severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
all_violations.sort(key=lambda v: severity_order.get(v.get("severity", "info"), 4))

result = json.dumps({
    "file_path": file_path,
    "total_violations": len(all_violations),
    "check_summaries": check_summaries,
    "all_violations": all_violations,
    "status": "aggregation_complete"
})
```

### Step 3: LLM synthesizes a senior-level review summary with prioritized recommendations

3. [llm: You are a senior software engineer performing a code audit review. Here is the aggregated violation data:

## File Audited
{file_path}

## Check Summaries
{check_summaries}

## All Violations (sorted by severity)
{all_violations}

Produce a senior-level review with these sections:

1. **Executive Summary** — 2-3 sentences on overall code quality and risk level.
2. **Critical Issues** — violations that should be fixed before commit (severity: critical/high).
3. **Recommended Improvements** — violations that are good to fix but not blocking (severity: medium/low/info).
4. **Positive Observations** — what the code does well (checks that returned zero violations).

Format as clean markdown. Be specific — reference line numbers and check names. Do not hallucinate violations not present in the data.]