---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Diff the working tree against the last commit and summarize what's uncommitted. Runs git diff deterministically, then the small model summarizes the meaningful changes (not whitespace). Use before a commit to verify what will be committed, or to check what's been changed since the last commit.
when_to_use: before committing, when checking what's changed since last commit, when verifying uncommitted edits are correct, or when asked 'what have I changed'
falsifiable_if: the summary reports changes that aren't in the diff, or misses meaningful uncommitted changes
applies_to:
  - git
  - diffing
  - verification
  - self-modification
allowed_tools:
  - code_read
  - llm_generate
summary: Git-Working-Diff
tags:
  - procedure
  - procedures
---

# Git-Working-Diff

## When to Run This

Run before committing to verify what will go in. Or run to check what's
been changed since the last commit.

## Steps

### Step 1: Run git diff deterministically

1. ```python
import subprocess, json

git_dir = Path(vault_path)
# Get the diff stat
r = subprocess.run(
    ["git", "diff", "--stat"],
    cwd=str(git_dir), capture_output=True, text=True, timeout=15)
stat = r.stdout.strip()

# Get the actual diff (unified, no whitespace noise)
r2 = subprocess.run(
    ["git", "diff", "-w", "--unified=1"],
    cwd=str(git_dir), capture_output=True, text=True, timeout=15)
diff = r2.stdout.strip()[:4000]  # cap for context budget

# Get list of changed files
r3 = subprocess.run(
    ["git", "diff", "--name-only"],
    cwd=str(git_dir), capture_output=True, text=True, timeout=15)
changed_files = [f for f in r3.stdout.strip().split('\n') if f]

result = json.dumps({
    "changed_files": changed_files,
    "stat": stat,
    "diff_preview": diff,
    "file_count": len(changed_files),
})
```

### Step 2: Small model summarizes the changes

2. ```python
import json as _json

data = _json.loads(output)
if data.get("file_count", 0) == 0:
    result = _json.dumps({"summary": "no uncommitted changes", "files": []})
else:
    prompt = f"""Summarize the uncommitted changes in this git working tree.
Changed files: {data['changed_files']}
Diff stat: {data['stat']}
Diff preview:
{data['diff_preview']}

Return JSON: {{"summary": "2-3 sentence summary", "files": [{{"file": "name", "change": "what changed"}}], "safe_to_commit": true/false, "concerns": ["any issues"]}}
Return ONLY the JSON."""
    summary = llm_generate(prompt)
    result = summary
```

### Step 3: Return the summary

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"summary": "could not parse git diff summary"}
result = _json.dumps(parsed)
```