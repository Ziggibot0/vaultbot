---
type: procedure
status: experimental
baseline: true
model_cartridge: medium
created: 2026-08-19
description: "The CI-failure feedback loop for pull requests. Given a PR number (or 'latest'), checks its CI status via pr_feedback, reads the failure annotations, diagnoses the root cause, applies a fix with safe_write/edit_lines, runs Run-CI-Gates locally to verify the fix, commits and pushes, then loops back to check CI again. Max 3 iterations. This is the workflow a human developer does when a PR fails CI — codified so VaultBot can do it autonomously."
when_to_use: "When a PR's CI has failed, when asked to 'fix the PR', 'iterate on the PR', 'the PR failed CI fix it', when pr_feedback shows failing checks, or when you submitted a contribution and want to verify it goes green."
falsifiable_if: "The procedure reports a PR as fixed when CI is still failing, or pushes a fix that doesn't address the actual CI error."
applies_to:
  - git-workflow
  - ci
  - github
  - self-modification
  - orchestration
  - iteration
allowed_tools:
  - code_read
  - code_run
  - safe_write
  - edit_lines
  - run_procedure
  - llm_generate
summary: Iterate-PR
tags:
  - procedure
  - procedures
  - ci
  - pr
  - iteration
  - github
  - orchestration
---

# Iterate-PR

## Purpose

This is the **CI-failure feedback loop**. When a pull request fails CI,
this procedure reads the failure, fixes it locally, verifies the fix
with local CI gates, pushes, and checks again. It's the exact workflow
a human developer does when GitHub Actions shows a red ❌ — codified
so VaultBot can do it autonomously.

## When to Run

- When a PR you submitted has failing CI checks
- When asked to "fix the PR" or "the PR failed"
- After `submit_contribution` to verify the PR goes green
- When `pr_feedback` shows failing check-runs

## Why This Exists

A PR that fails CI needs a fix-and-verify loop, but doing it manually is
slow and error-prone. This procedure codifies the human developer's
workflow — read the failure, fix locally, verify with local CI gates, push,
re-check — so VaultBot can do it autonomously. The tradeoff: it caps at 3
iterations to prevent infinite loops, handing off to a human if the fix
isn't found.

## What It Does

For each iteration (max 3):

1. **Check PR status** — calls `pr_feedback` to get CI state + failure annotations
2. **If CI is green** — done, report success
3. **If CI failed** — read the failure annotations (exact error messages)
4. **Diagnose** — the LLM identifies the root cause from the annotations
5. **Reproduce locally** — runs `Run-CI-Gates` to confirm the failure happens locally
6. **Fix** — applies the fix with `safe_write` or `edit_lines`
7. **Verify locally** — runs `Run-CI-Gates` again to confirm the fix works
8. **Commit + push** — commits the fix and pushes to the PR branch
9. **Loop** — back to step 1 to check if CI is now green

## Why Local CI Gates Matter

The key insight: **run CI gates locally before pushing**. This catches
failures in seconds instead of waiting minutes for GitHub Actions. If
`ruff format --check` fails locally, fix it locally, verify it passes
locally, THEN push. This avoids the push → wait → fail → push cycle.

## Safety

- The fix is written with `safe_write` (import-graph verified, auto-rollback)
- Local CI gates must pass before pushing
- Max 3 iterations to prevent infinite loops
- If a fix can't be found in 3 iterations, reports the remaining failures
  for a human to take over

## Steps

### Step 1: Check the PR's CI status and track iteration count

This step calls `pr_feedback` to get the current state of the PR's CI
checks, failure annotations, and review comments. It also tracks the
iteration count to enforce the max-3-iterations limit deterministically.

```python
import json

pr_number = args.get("pr_number")
if not pr_number:
    result = json.dumps({"error": "pr_number argument required (or pass 'latest' for most recent)"})
else:
    # Track iteration count — args.get("iteration", 1) defaults to 1
    # The caller passes iteration=N on re-runs to enforce the max-3 limit
    iteration = int(args.get("iteration", 1))
    if iteration > 3:
        result = json.dumps({
            "error": "Max iterations (3) reached. Remaining failures require manual review.",
            "iteration": iteration,
        })
    else:
        from custom_tools.pr_feedback import run as _feedback
        pr_number_int = int(pr_number) if str(pr_number).isdigit() else pr_number
        feedback = _feedback({"pr_number": pr_number_int})
        feedback["_iteration"] = iteration
        result = json.dumps(feedback, default=str)
```

### Step 2: Diagnose — is the PR green or failing?

[llm: Examine the PR feedback from Step 1. Determine:
- **CI state**: Are all checks passing (green), failing (red), or pending?
- **If failing**: List the exact failure annotations — the error messages,
  which files they're in, which gate failed (ruff check, ruff format, pyright,
  pytest, etc.).
- **If green or merged**: Report SUCCESS — the PR is healthy. No iteration needed.
- **If pending**: Report PENDING — CI is still running. Suggest waiting and re-running.

Output a JSON verdict:
  {"verdict": "green" | "failures" | "pending" | "merged", "failures": [...], "root_cause": "..."}

If verdict is "green", "merged", or "pending", STOP here — no fix needed.
If verdict is "failures", proceed to Step 3.]

### Step 3: Reproduce the failure locally with Run-CI-Gates

Before fixing anything, confirm the failure happens locally. This avoids
pushing a "fix" that doesn't actually address the problem.

```python
import json

# Run the local CI gates to reproduce the failure
proc_result = run_procedure("Run-CI-Gates", {})
result = json.dumps(proc_result, default=str) if isinstance(proc_result, (dict, list)) else str(proc_result)
```

### Step 4: Diagnose the root cause and plan the fix

[llm: You are fixing a CI failure on a VaultBot PR. You have:
1. The PR failure annotations from Step 2 (the GitHub CI errors)
2. The local CI gate results from Step 3 (reproduced locally)

Compare them — do the local gates show the same failure? If yes, the
failure is reproducible. If the local gates pass but CI failed, the
issue may be environment-specific (Python version, missing dependency).

Identify the root cause and the exact fix needed:
- Which file(s) need to change
- What the change is (be specific — "collapse the run_git() call onto one line" not "fix formatting")
- Which gate it will fix (ruff_check / ruff_format / pyright / pytest)

Read the relevant file(s) with code_read to see the current code and
confirm your diagnosis.

Output a fix plan:
  {"root_cause": "...", "file": "...", "change": "...", "gate_fixed": "..."}]

### Step 5: Apply the fix

[llm: Implement the fix plan from Step 4. Read the current file content
with code_read if needed, then apply the fix. Use the appropriate tool:
- For a targeted string replacement: use edit_lines or safe_replace
- For a full-file rewrite: use safe_write (which verifies the import graph
  and rolls back on failure)

Keep the change MINIMAL and TARGETED — fix exactly the CI error, nothing
else. Do not introduce unrelated changes, refactors, or improvements.
The goal is a clean fix that turns the red ❌ green.

After applying the fix, report what you changed.]

### Step 6: Verify the fix locally with Run-CI-Gates

This is the critical step — **verify locally before pushing**. Run the
same CI gates that GitHub Actions runs. If they pass locally, they'll
pass on GitHub. If they fail locally, fix again before pushing.

```python
import json

# Re-run local CI gates to verify the fix
proc_result = run_procedure("Run-CI-Gates", {})
result = json.dumps(proc_result, default=str) if isinstance(proc_result, (dict, list)) else str(proc_result)
```

### Step 7: Check if local gates pass

[llm: Examine the local CI gate results from Step 6. Did all gates pass?
- If ALL PASS: Proceed to Step 8 (commit and push the fix).
- If any gate STILL FAILS: Do NOT push. Go back to Step 4 to diagnose
  the remaining failure and apply another fix. If this is the 3rd
  iteration, STOP and report the remaining failures for a human.

Output: {"verdict": "pass" | "fail", "remaining_failures": [...]}]

### Step 8: Commit and push the fix

Local gates are green — now push the fix to the PR branch.

```python
import json, subprocess, os, sys

# Use _find_git_root from upstream_identity for proper git root detection.
# This walks up from the backend dir to find the actual .git directory,
# handling both layouts (vault root IS the git repo, or git repo is one
# level up like vaultbot-fork/ containing vaultbot/).
backend_dir = str(Path(FRAMEWORK_ROOT) / "vaultbot_backend")
sys.path.insert(0, backend_dir)
try:
    from upstream_identity import _find_git_root
    git_root = _find_git_root(backend_dir)
except Exception:
    # Fallback: manual walk
    git_root = None
    p = Path(backend_dir)
    while p != p.parent:
        if (p / ".git").exists():
            git_root = str(p)
            break
        p = p.parent
    if git_root is None:
        git_root = str(Path(vault_path))

# Get the current branch name
r = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    capture_output=True, text=True, cwd=git_root, timeout=30,
)
current_branch = r.stdout.strip()

# Stage all changes in the backend
r2 = subprocess.run(
    ["git", "add", "-A"],
    capture_output=True, text=True, cwd=git_root, timeout=30,
)

# Commit with a descriptive message
commit_msg = args.get("commit_message", "fix: resolve CI gate failure")
r3 = subprocess.run(
    ["git", "commit", "-m", commit_msg],
    capture_output=True, text=True, cwd=git_root, timeout=30,
)

# Push to the current branch's upstream
r4 = subprocess.run(
    ["git", "push"],
    capture_output=True, text=True, cwd=git_root, timeout=60,
)

# Get the current iteration count
iteration = int(args.get("iteration", 1))

result = json.dumps({
    "branch": current_branch,
    "commit_exit": r3.returncode,
    "commit_output": (r3.stdout + r3.stderr).strip(),
    "push_exit": r4.returncode,
    "push_output": (r4.stdout + r4.stderr).strip(),
    "iteration": iteration,
    "iterations_remaining": max(0, 3 - iteration),
})
```

### Step 9: Loop back — report iteration status

After pushing, GitHub Actions will re-run CI on the new commit. Wait a
moment, then check the PR status again.

```python
import json

# Read iteration info from Step 8's output
push_result = json.loads(output)
iteration = push_result.get("iteration", 1)
remaining = push_result.get("iterations_remaining", 0)

if remaining > 0:
    result = json.dumps({
        "iteration_complete": True,
        "iteration": iteration,
        "iterations_remaining": remaining,
        "next_action": f"Re-run Iterate-PR with iteration={iteration + 1} to check if CI is now green.",
        "hint": "Wait ~30s for GitHub Actions to start, then re-run with the next iteration number.",
    })
else:
    result = json.dumps({
        "iteration_complete": True,
        "iteration": iteration,
        "iterations_remaining": 0,
        "next_action": "Max iterations reached. Check the PR manually — the fix was pushed but CI may still be failing.",
        "hint": "If CI is still red, a human should review the remaining failures.",
    })
```

## Related

- [[Run-CI-Gates]] — the local CI gates this procedure runs to verify fixes
- [[Submit-Contribution]] — the workflow that produces the PR this iterates on
- [[Solve-GitHub-Issue]] — end-to-end issue resolution workflow