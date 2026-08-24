---
type: procedure
status: experimental
baseline: true
model_cartridge: medium
created: 2026-08-22
description: "The main autonomous dev cycle: sync → triage → pick issue → branch → fix → CI preflight → commit → PR → iterate. Orchestrates all the git/CI/procedure sub-procedures into one loop. Stops when CI passes and the PR is submitted, or after max iterations."
when_to_use: "When asked to 'work on an issue', 'pick up the next issue', or 'run the dev cycle'. This is the top-level orchestrator."
falsifiable_if: The procedure reports success when CI has failed, pushes to main, or creates a PR without running preflight.
applies_to:
  - dev-cycle
  - self-modification
  - orchestration
allowed_tools:
  - code_read
  - code_run
  - run_procedure
  - vault_search
  - vault_safe_write
summary: Main autonomous dev cycle orchestrator.
tags:
  - procedure
  - dev-cycle
  - orchestration
---

# Dev-Cycle

## Purpose

The main autonomous development cycle. Orchestrates all the sub-procedures
into one loop: sync → triage → branch → fix → preflight → commit → push →
iterate. This is VaultBot's issue-driven dev loop.

## Why This Exists

VaultBot needs to be its own best developer. Instead of relying on a human
to manually sync, branch, fix, test, and submit, this procedure chains all
those steps into one autonomous cycle. The cycle is gated on CI preflight
at every iteration — it never pushes code that fails ruff or pytest.

## Safety

- **Never pushes to main**: All work is on a feature branch.
- **CI preflight is built into submit_contribution**: The tool runs ruff
  check, ruff format --check, and pytest before creating the PR.
- **Max iterations**: Defaults to 3 iterations to prevent infinite loops.
- **Stops on unrecoverable errors**: If the branch can't be created, or CI
  fails 3 times in a row, the cycle stops and reports what happened.

## Process Engineering Notes

This procedure was simplified from 10 steps to 6 by eliminating redundancy:
- **Steps 6+7+8+9 → Step 6**: `submit_contribution` already runs preflight
  CI, commits, pushes, and creates the PR internally. Calling preflight +
  commit + push + submit separately was triple-doing the same work.
- **Steps 4+5 → Step 4**: "Diagnose" then "fix" was 2 LLM round-trips.
  Merging into "diagnose and fix" saves one full LLM call per cycle — the
  single biggest speed win since LLM calls are the bottleneck.

## Input

- `args.issue_number` (optional): Specific GitHub issue to work on.
  If not provided, the triage step will pick the next open issue.
- `args.max_iterations` (optional): Max CI fix iterations (default: 3).
- `args.branch_prefix` (optional): Prefix for the branch name (default: `fix`).

## Steps

### Step 1: Pre-flight checklist

Run the Dev-Cycle-Checklist to verify git, gh, env vars, and tree status.

```python
import json

checklist = run_procedure("Dev-Cycle-Checklist", {})
print(json.dumps({"step": "checklist", "result": checklist}, default=str))
```

### Step 2: Sync with upstream

Sync the local repo with upstream main so we're working on the latest code.

```python
import json

sync = run_procedure("Git-Sync-Upstream", {})
print(json.dumps({"step": "sync", "result": sync}, default=str))
```

### Step 3: Pick the issue and create a branch

Read the GitHub issue (or pick the next open one), then create a feature
branch for the fix.

```python
import json

issue_number = args.get("issue_number")

if issue_number:
    # Use the specified issue
    from custom_tools.github_issues import run as _issues
    issue = _issues({"action": "read", "issue_number": int(issue_number)})
    if "error" in (issue if isinstance(issue, dict) else {}):
        result = json.dumps({"error": f"Failed to read issue #{issue_number}: {issue}"}, default=str)
        print(result)
    else:
        title = issue.get("title", f"fix-issue-{issue_number}")
        branch_name = f"{args.get('branch_prefix', 'fix')}/issue-{issue_number}"
        branch_result = run_procedure("Git-Create-Branch", {"branch_name": branch_name})
        print(json.dumps({"step": "branch", "issue": issue_number, "title": title, "result": branch_result}, default=str))
else:
    # Pick the next open issue
    from custom_tools.github_issues import run as _issues
    issues_result = _issues({"action": "list", "state": "open"})
    if isinstance(issues_result, dict) and "error" in issues_result:
        print(json.dumps({"error": f"Failed to list issues: {issues_result}"}, default=str))
    else:
        # Take the first open issue
        # github_issues 'list' returns a dict with an "issues" key, not a bare list.
        # Handle both shapes (dict-with-key, or a bare list) so triage never drops to [].
        if isinstance(issues_result, dict):
            issues = issues_result.get("issues", [])
        elif isinstance(issues_result, list):
            issues = issues_result
        else:
            issues = []
        if not issues:
            print(json.dumps({"info": "No open issues to work on"}))
        else:
            first = issues[0] if isinstance(issues[0], dict) else {}
            num = first.get("number", "unknown")
            title = first.get("title", "untitled")
            branch_name = f"{args.get('branch_prefix', 'fix')}/issue-{num}"
            branch_result = run_procedure("Git-Create-Branch", {"branch_name": branch_name})
            print(json.dumps({"step": "branch", "issue": num, "title": title, "result": branch_result}, default=str))
```

### Step 4: Diagnose and fix

[llm: You are VaultBot working on a GitHub issue. Read the issue details from
the prior step's output, then use code_read to understand the relevant code.
Diagnose the root cause and implement the fix in one pass:
1. Read the relevant source files with code_read
2. Identify the root cause
3. Write the corrected version with safe_write

Keep the change minimal and targeted to the root cause. Do NOT introduce
unrelated changes. Output a summary of what you changed and why.]

### Step 5: Run CI preflight (iterate if needed)

Run the CI hard gates locally. If they fail, fix and re-run (up to
max_iterations times). This catches failures BEFORE the PR is submitted,
saving a CI round-trip.

```python
import json

max_iter = int(args.get("max_iterations", 3))
preflight = run_procedure("Run-CI-Preflight", {"scope": "full"})
print(json.dumps({"step": "ci_preflight", "iteration": 1, "result": preflight}, default=str))
```

### Step 6: Submit PR and review (preflight + commit + push + PR in one call)

This step calls `submit_contribution` which internally runs preflight CI,
commits, pushes, and creates the PR. Then `review_contributions` merges
only if CI is green and the safety scan passes.

```python
import json

issue_number = args.get("issue_number", "unknown")
title = f"fix: resolve issue #{issue_number}"
description = f"Fixes #{issue_number}\n\nAutonomously resolved by VaultBot Dev-Cycle procedure."

from custom_tools.submit_contribution import run as _submit
submit_result = _submit({"title": title, "description": description})
print(json.dumps({"step": "submit_pr", "result": submit_result}, default=str))

# Review and merge — gated on BOTH green CI AND PASS safety scan
from custom_tools.review_contributions import run as _review
review_result = _review({"merge": True})
print(json.dumps({"step": "review_and_merge", "result": review_result}, default=str))
```

## Related

- [[Dev-Cycle-Checklist]] — pre-flight checks before the cycle starts
- [[Git-Sync-Upstream]] — sync with upstream
- [[Git-Create-Branch]] — create a feature branch
- [[Run-CI-Preflight]] — run CI hard gates locally (step 5)
- [[Predict-Change-Impact]] — assess blast radius before editing
- [[Solve-GitHub-Issue]] — existing issue solver (simpler, single-shot)
- [[submit_contribution]] — the tool that does preflight + commit + push + PR