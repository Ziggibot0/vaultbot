---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-03
description: Audit the VaultBot backend after external changes (e.g. GitHub Copilot edits). Runs git diff to get exact changes, greps for safety-critical patterns (sliding window, failsafe, checkpointing, MAX_ROUNDS, tool dispatch, per-step RAG), lists new/removed files, and feeds all results to the small model for a final risk assessment + report. Zero big-model cost. Use when the user says 'audit the changes' or 'what did Copilot change' or after any external code modification.
when_to_use: after external code changes (Copilot, manual edits, PRs), when asked to audit the backend, when checking system health after modifications
falsifiable_if: the procedure reports changes that don't exist, or misses actual changes (git diff is ground truth)
applies_to:
  - code-audit
  - system-health
  - post-change-verification
  - cost-optimization
allowed_tools:
  - code_read
  - llm_generate
summary: Post-Copilot-Audit
tags:
  - procedure
  - procedures
---

# Post-Copilot-Audit

## When to Run This

After any external modification to the VaultBot backend (Copilot edits,
manual changes, merged PRs). Produces a structured audit report identifying
what changed, whether safety-critical patterns survived, and a risk
assessment — all without using the big model.

## Steps

### Step 1: Get the git diff (deterministic)

This step runs `git diff` against the last commit to get the exact changes.
Zero LLM cost.

1. ```python
import subprocess, json, os

vault_path = os.environ.get("VAULT_PATH", "")
backend_dir = os.path.join(vault_path, "vaultbot", "vaultbot_backend")

# Get the diff stat (summary of changes)
try:
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--stat"],
        capture_output=True, text=True, cwd=backend_dir,
        timeout=30,
    )
    diff_stat = result.stdout if result.returncode == 0 else f"ERROR: {result.stderr}"
except Exception as e:
    diff_stat = f"ERROR running git diff --stat: {e}"

# Get the full diff (limited to 5000 chars to avoid blowing context)
try:
    result_full = subprocess.run(
        ["git", "diff", "HEAD~1"],
        capture_output=True, text=True, cwd=backend_dir,
        timeout=30,
    )
    full_diff = result_full.stdout[:5000] if result_full.returncode == 0 else f"ERROR: {result_full.stderr}"
except Exception as e:
    full_diff = f"ERROR running git diff full: {e}"

# Get list of changed file names
try:
    result_names = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only"],
        capture_output=True, text=True, cwd=backend_dir,
        timeout=30,
    )
    changed_files = result_names.stdout.strip().split("\n") if result_names.returncode == 0 else []
except Exception as e:
    changed_files = []

print(json.dumps({"diff_stat": diff_stat, "full_diff_excerpt": full_diff, "changed_files": changed_files}, indent=2))
```

### Step 2: Grep for safety-critical patterns (deterministic)

This step greps the changed files for safety-critical patterns. Zero LLM cost.

2. ```python
import subprocess, json, os, re

vault_path = os.environ.get("VAULT_PATH", "")
backend_dir = os.path.join(vault_path, "vaultbot", "vaultbot_backend")

# Safety-critical patterns to check
patterns = {
    "sliding_window": r"sliding.window|MAX_HISTORY|context_window",
    "failsafe": r"failsafe|fail.safe|FAILSAFE",
    "checkpointing": r"checkpoint|Checkpoint|CHECKPOINT",
    "max_rounds": r"MAX_ROUNDS|max_rounds",
    "tool_dispatch": r"tool_dispatch|handle_tool|execute_tool",
    "per_step_rag": r"per.step.rag|step_context|fused_retrieval",
}

# Get list of changed files from git diff
try:
    result_files = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only"],
        capture_output=True, text=True, cwd=backend_dir,
        timeout=30,
    )
    changed_files = result_files.stdout.strip().split("\n") if result_files.returncode == 0 else []
except Exception as e:
    changed_files = []

# Grep each changed file for safety patterns
pattern_results = {}
for fname in changed_files:
    if not fname or not fname.endswith(".py"):
        continue
    fpath = os.path.join(backend_dir, fname)
    if not os.path.exists(fpath):
        pattern_results[fname] = {"error": "FILE NOT FOUND"}
        continue
    try:
        with open(fpath, 'r') as f:
            content = f.read()
    except Exception as e:
        pattern_results[fname] = {"error": str(e)}
        continue
    file_patterns = {}
    for pname, regex in patterns.items():
        matches = re.findall(regex, content, re.IGNORECASE)
        file_patterns[pname] = {"found": len(matches) > 0, "count": len(matches)}
    pattern_results[fname] = file_patterns

print(json.dumps(pattern_results, indent=2))
```

### Step 3: List file structure changes (deterministic)

This step lists the backend directory structure and file sizes. Zero LLM cost.

3. ```python
import os, json, subprocess

vault_path = os.environ.get("VAULT_PATH", "")
backend_dir = os.path.join(vault_path, "vaultbot", "vaultbot_backend")

# List all .py files in the backend
py_files = []
for root, dirs, files in os.walk(backend_dir):
    for f in files:
        if f.endswith(".py"):
            rel = os.path.relpath(os.path.join(root, f), backend_dir)
            py_files.append(rel)
py_files.sort()

# Get file sizes for changed files
try:
    result_names = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only"],
        capture_output=True, text=True, cwd=backend_dir,
        timeout=30,
    )
    changed_files = result_names.stdout.strip().split("\n") if result_names.returncode == 0 else []
except Exception:
    changed_files = []

file_sizes = {}
for fname in changed_files:
    if not fname:
        continue
    fpath = os.path.join(backend_dir, fname)
    if os.path.exists(fpath):
        file_sizes[fname] = os.path.getsize(fpath)

print(json.dumps({"total_py_files": len(py_files), "py_files": py_files[:50], "changed_file_sizes": file_sizes}, indent=2))
```

### Step 4: Extract changed file contents (deterministic)

This step reads the full content of each changed .py file for the LLM step to analyze. Zero LLM cost.

4. ```python
import os, json, subprocess

vault_path = os.environ.get("VAULT_PATH", "")
backend_dir = os.path.join(vault_path, "vaultbot", "vaultbot_backend")

# Re-run git diff --name-only to get the list (code steps don't share state)
result = subprocess.run(
    ["git", "diff", "HEAD~1", "--name-only"],
    capture_output=True, text=True, cwd=backend_dir,
    timeout=30,
)
changed_files = result.stdout.strip().split("\n") if result.returncode == 0 else []

file_contents = {}
for fname in changed_files:
    if not fname or not fname.endswith(".py"):
        continue
    fpath = os.path.join(backend_dir, fname)
    if not os.path.exists(fpath):
        file_contents[fname] = "FILE NOT FOUND"
        continue
    try:
        with open(fpath, 'r') as f:
            file_contents[fname] = f.read()[:3000]  # limit to 3k chars per file
    except Exception as e:
        file_contents[fname] = f"ERROR: {e}"

print(json.dumps(file_contents, indent=2))
```

### Step 5: Generate the audit report (small model)

This step feeds all the deterministic results to the small model to write
the final audit report. This is the only step that uses any model — and
it's the small model, not the big model.

5. [llm: You are a code auditor. Based on the following deterministic analysis
of changes to the VaultBot backend, write a structured audit report.

The report must include:
- **Changed Files**: List each changed file and what changed.
- **Safety Pattern Check**: For each safety-critical pattern (sliding window,
  failsafe, checkpointing, MAX_ROUNDS, tool dispatch, per-step RAG),
  report whether it was found in the changed files.
- **Risk Assessment**: Rate the risk as LOW, MEDIUM, or HIGH, with reasoning.
- **Recommendations**: Any actions needed (re-test, restart, investigate).

Format as markdown with clear sections.]

validation: contains "Changed Files" and "Safety Pattern Check" and "Risk Assessment"

## Notes

- Steps 1-4 are pure deterministic code: git diff, grep, file listing, file reading.
  Zero LLM cost. They gather all the data the small model needs.
- Step 5 uses the small model (model_cartridge: small) to synthesize the
  report from the deterministic data. This is the only model cost — and
  it's the small model running locally, not the big cloud model.
- Compare to the previous approach: the big model was used for the ENTIRE
  audit — reading files, understanding changes, checking patterns, writing
  the report. Now 95% of the work is deterministic code and only the final
  synthesis uses the small model.
- The procedure can be chained after [[Route-Task]] as the audit branch.