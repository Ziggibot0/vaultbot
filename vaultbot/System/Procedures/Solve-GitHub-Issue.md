---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-19
description: "Solve a GitHub issue end-to-end: read the issue, locate the relevant code via the codebase map, fix it with safe_write, run the test suite, submit a PR, and merge it only if CI is green and the safety scan passes. Orchestrates existing procedures — no duplicated logic."
when_to_use: "When asked to 'solve issue #N', 'fix the GitHub issue', or when triaging open issues to fix autonomously."
falsifiable_if: "The procedure merges a PR whose CI is not green, or reports success without actually fixing the issue."
applies_to:
  - github
  - self-modification
  - orchestration
  - issue-solving
allowed_tools:
  - code_read
  - run_procedure
summary: Solve-GitHub-Issue
tags:
  - procedure
  - procedures
  - github
  - orchestration
---

# Solve-GitHub-Issue

## Purpose

Solve a GitHub issue end-to-end. This is the orchestrator that chains the
community-contribution tools and existing procedures into one autonomous
loop: read → understand → fix → test → submit → review → merge.

## When to Run

- When asked "solve issue #N" or "fix the GitHub issue"
- When triaging open issues to fix autonomously

## Safety

- The merge step is gated on BOTH a PASS safety scan AND green CI
  (enforced inside `review_contributions`). A PR whose build state can't
  be confirmed is never merged.
- The fix is written with `safe_write` (import-graph verified) and tested
  with `Run-Test-Suite` before any PR is opened.

## Steps

### Step 1: Read the issue and locate the relevant code

This step reads the GitHub issue's title, body, and comments, then
regenerates the codebase map so the fix step knows where to look.

```python
import json

issue_number = args.get("issue_number")
if not issue_number:
    result = json.dumps({"error": "issue_number argument required"})
else:
    # Read the issue via the github_issues tool.
    from custom_tools.github_issues import run as _issues
    issue = _issues({"action": "read", "issue_number": int(issue_number)})
    if "error" in issue:
        result = json.dumps({"error": issue["error"]})
    else:
        # Ensure the codebase map is fresh, then surface it for the fix step.
        _map = run_procedure("Codebase-Map", {})
        result = json.dumps({
            "issue": issue,
            "codebase_map_status": _map.get("overall_passed"),
        })
print(result)
```

### Step 2: Diagnose the root cause and plan the fix

[llm: You are solving a GitHub issue for VaultBot's own codebase. Given the
issue body and the codebase map (regenerated in Step 1), identify the root
cause and the exact file(s) + function(s) that need to change. Read the
relevant source with code_read to confirm. Output a concise fix plan:
- root_cause: one sentence
- files_to_change: list of file paths
- change_description: what to change in each file
Do NOT write any code yet — just the plan.]

### Step 3: Apply the fix with safe_write

[llm: Implement the fix plan from Step 2. For each file, read its current
content with code_read, then write the corrected version with safe_write
(which verifies the import graph and rolls back on failure). Keep the change
minimal and targeted to the root cause. Do NOT introduce unrelated changes.]

### Step 4: Run the test suite

This step runs the pytest suite to confirm the fix didn't break anything.

```python
import json

result = run_procedure("Run-Test-Suite", {"filter": "not step_gate"})
output = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)
print(output)
```

### Step 5: Submit the fix as a PR

This step submits the fix as a pull request for review.

```python
import json

issue_number = args.get("issue_number")
title = args.get("title", f"fix: resolve issue #{issue_number}")
description = args.get("description", f"Fixes #{issue_number}")

from custom_tools.submit_contribution import run as _submit
result = _submit({"title": title, "description": description})
output = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)
print(output)
```

### Step 6: Review and merge (CI-gated)

This step reviews the PR and merges it only if CI is green and the safety
scan passes.

```python
import json

# review_contributions with merge=True now requires BOTH a PASS safety
# scan AND green CI before merging. It will not merge a broken build.
from custom_tools.review_contributions import run as _review
result = _review({"merge": True})
output = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)
print(output)
```
