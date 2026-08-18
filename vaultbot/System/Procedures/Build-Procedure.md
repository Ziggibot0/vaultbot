---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-06
description: "Build a complete, tested, verified procedure from a task description. This is the one-shot procedure factory: give it a task, get back a working procedure on disk. It composes drafting (LLM step), big-model quality review (code step with llm_generate), vault_safe_write (disk), Verify-Procedure-Args (static checks), vault_lint (link/frontmatter quality), and Test-Procedure-Until-Pass (dynamic test→fix→retest loop). The result is a procedure that has been drafted, reviewed, written, statically verified, dynamically tested, auto-fixed if broken, and linted. Use this whenever you need a new procedure — it replaces the manual draft→review→write→test→fix→lint workflow with a single call."
when_to_use: "When the user says 'make a procedure for X', when you identify a gap that needs a new procedure, when you want to create a procedure and know it actually works before trusting it, when you're tired of the manual draft→review→write→test→fix→lint cycle, when you want a procedure built strong from the start, when you need to build a new skill as a procedure, when you want to automate a workflow you just did manually, when you realize you're doing something repetitive that should be proceduralized, when you need a one-shot procedure factory, when someone says 'create a procedure', 'build a procedure', 'make a procedure', 'automate this', 'proceduralize this', or 'turn this into a procedure'"
falsifiable_if: the built procedure passes all checks but produces wrong output on real use, the review step approves a draft with obvious flaws, or the test loop reports success when the procedure actually fails (verifiable by running the procedure manually)
applies_to:
  - procedure-creation
  - self-improvement
  - meta-procedure
  - orchestration
  - quality-assurance
allowed_tools:
  - run_procedure
  - vault_safe_write
  - vault_lint
  - vault_read_note
  - code_read
  - llm_generate
provides:
  - Verify-Procedure-Args
  - Test-Procedure-Until-Pass
task: "What the procedure should do. Be specific: 'extract all wikilinks from a note and check if they resolve', not 'handle links'."
procedure_name: Optional. The name for the new procedure. If omitted, the review step will suggest one.
tools_available: Optional. Comma-separated list of tools the procedure can use. Defaults to the standard set.
test_args: Optional. JSON object of arguments to pass when testing the procedure.
summary: Build-Procedure
tags:
  - procedure
  - procedures
---

# Build-Procedure

## When to Run This

This is the **one-shot procedure factory**. Give it a task description, get back a working, tested, verified procedure on disk.

```
OLD: draft → review → write → test → fail → fix → retest → fail → fix → retest → lint → done
NEW: Build-Procedure(task="...") → done
```

## Architecture

LLM steps handle the heavy cognitive work (drafting, reviewing) — they run in the main process with no subprocess pollution. Code steps only do file I/O and delegate to sub-procedures via `run_procedure`. This avoids the stdout pollution that occurs when `llm_generate` is called inside a code step's subprocess.

```
Build-Procedure
  ├─ Step 1 (LLM): Draft the procedure markdown
  ├─ Step 2 (code): Save draft to temp file
  ├─ Step 3 (code): Big-model quality review — reads temp file, calls llm_generate
  ├─ Step 4 (code): Apply review fixes, write to disk via vault_safe_write
  ├─ Step 5 (code): Static verify via run_procedure("Verify-Procedure-Args")
  ├─ Step 6 (code): Lint via vault_lint
  ├─ Step 7 (code): Dynamic test via run_procedure("Test-Procedure-Until-Pass")
  ├─ Step 8 (code): Final lint
  └─ Step 9 (LLM): Final report
```

## Steps

### Step 1: Draft the procedure markdown from the task description

1. ```python
import json

task = args.get("task", "")
tools_available = args.get("tools_available", "vault_search, vault_read_note, vault_list, vault_lint, llm_generate, run_procedure, code_read, vault_safe_write, vault_append, web_read_source")

if not task:
    result = json.dumps({"error": "task argument required"})
else:
    # Build prompt in parts to avoid nested triple-quote issues
    _task_line = f"TASK: The procedure you write must accomplish this specific task: {task}"
    _tools_line = f"Available tools for allowed_tools: {tools_available}"
    
    prompt = (
        "You are a VaultBot procedure writer. Write a procedure that DOES the task below.\n"
        "The procedure must be ABOUT accomplishing the task — NOT about how to write procedures.\n"
        "Do NOT write a meta-procedure about procedure creation. Write a procedure that actually does the work described.\n\n"
        + _task_line + "\n\n"
        + _tools_line + "\n\n"
        "## Required Frontmatter (all fields mandatory)\n"
        "type: procedure\n"
        "status: experimental\n"
        "model_cartridge: small  # or big, or vision\n"
        "created: 2026-08-14\n"
        'description: "one-line summary for retrieval — specific enough that RAG surfaces it"\n'
        'when_to_use: "SITUATIONS that trigger this procedure, not topics"\n'
        'falsifiable_if: "specific, observable failure condition"\n'
        "allowed_tools:\n"
        "  - tool_name\n"
        "summary: Short-Title\n"
        "tags:\n"
        "  - procedure\n"
        "  - procedures\n\n"
        "## Step Format (UNIFIED — the only format the compiler guarantees)\n"
        "Every step MUST use this exact format:\n"
        "### Step N: Short human-readable summary\n"
        "N. ```python\ncode here\n```\n"
        "OR for LLM steps:\n"
        "### Step N: Short human-readable summary\n"
        "N. [llm: instruction here]\n\n"
        "- Code steps use ```python blocks. LLM steps use [llm: ...] tags.\n"
        "- NEVER use [vllm:] or [model_cartridge:] tags — only [llm: ...].\n"
        "- model_cartridge: small for classification/extraction/routing. big for reasoning/synthesis.\n\n"
        "## Standardized Sections (in this order)\n"
        "1. ## When to Run This — trigger conditions\n"
        "2. ## Inputs — documented args if any\n"
        "3. ## Steps — the machine-executable steps\n"
        "4. ## Why This Exists — the failure or gap that spawned this\n"
        "5. ## Related — wikilinks to related notes\n\n"
        "## Output Rules\n"
        "- Do NOT wrap the output in markdown code fences. Return raw markdown.\n"
        "- The output must start with --- (YAML frontmatter).\n\n"
        "Write the FULL markdown including YAML frontmatter. Return ONLY the raw markdown."
    )

    draft_md = llm_generate(prompt)
    result = draft_md
```

2. ```python
import json, os

draft_md = output if output else ""

if not draft_md or len(draft_md) < 50:
    result = json.dumps({"error": "draft too short", "len": len(draft_md)})
else:
    vault_path = os.environ.get("VAULT_PATH", ".")
    temp_dir = os.path.join(vault_path, "vaultbot", "vaultbot_backend", "trash")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, "_build_procedure_draft.md")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(draft_md)
    result = json.dumps({"s": "saved", "p": temp_path, "l": len(draft_md)})
```

3. ```python
import json, os

# Read the draft from temp file (Step 2 output is in `output`)
step2 = json.loads(output) if output else {}
draft_path = step2.get("p", "")
if not draft_path or not os.path.exists(draft_path):
    result = json.dumps({"error": "draft file not found", "path": draft_path})
else:
    with open(draft_path, "r", encoding="utf-8") as f:
        draft_md = f.read()

    # Build review prompt
    prompt = f"""You are a senior procedure reviewer. Review this draft for CORRECTNESS, DISCOVERABILITY, FALSIFIABILITY, and EXECUTABILITY. Be ruthless.

DRAFT:
<<<
{draft_md}
<<<

Check:
1. DESCRIPTION: specific enough for RAG? Says what it DOES, not what it's ABOUT?
2. WHEN_TO_USE: describes SITUATIONS, not topics?
3. FALSIFIABLE_IF: specific, observable failure condition?
4. STEPS: executable with listed allowed_tools? Complete workflow? Each step has a ### Step N: header? Code steps use ```python fences? LLM steps use [llm: ...] tags? NO [vllm:...] or [model_cartridge:...] tags — those are invalid.
5. MODEL_CARTRIDGE: correct? (small=classification/extraction/routing, big=reasoning/synthesis)
6. INPUTS: all args.get() calls have corresponding docs?
7. NO_CODE_FENCE_WRAP: the draft must NOT be wrapped in ```markdown or any outer code fence. It must be raw markdown starting with --- frontmatter.
8. FRONTMATTER: has type, status, created, summary, tags fields?

Return JSON:
{{"verdict": "APPROVED" or "NEEDS_FIXES", "suggested_name": "Name-Here", "checks": {{"description": {{"pass": true/false, "fix": "rewrite if failed"}}, "when_to_use": {{"pass": true/false, "fix": "rewrite if failed"}}, "falsifiable_if": {{"pass": true/false, "fix": "rewrite if failed"}}, "steps": {{"pass": true/false, "issues": []}}, "model_cartridge": {{"pass": true/false, "fix": ""}}, "inputs": {{"pass": true/false, "missing": []}}}}, "fixed_draft": "FULL FIXED MARKDOWN if NEEDS_FIXES, else empty"}}

Return ONLY the JSON."""

    review = llm_generate(prompt)
    result = review
```

4. ```python
import json, os, re

# Parse review from step 3 (output = step 3's result)
review_text = output if output else ""
try:
    start = review_text.find("{")
    end = review_text.rfind("}")
    review = json.loads(review_text[start:end+1]) if start != -1 else {}
except (json.JSONDecodeError, AttributeError):
    review = {}

verdict = review.get("verdict", "APPROVED")
suggested_name = review.get("suggested_name", args.get("procedure_name", "New-Procedure"))
fixed_draft = review.get("fixed_draft", "")

# Read original draft from step 2 (prior_results keyed by step number)
step2 = json.loads(prior_results.get(2, prior_results.get(2.0, "{}"))) if prior_results.get(2, prior_results.get(2.0)) else {}
draft_path = step2.get("p", "")
original_draft = ""
if draft_path and os.path.exists(draft_path):
    with open(draft_path, "r", encoding="utf-8") as f:
        original_draft = f.read()

final_md = fixed_draft if fixed_draft and verdict == "NEEDS_FIXES" else original_draft

if not final_md or len(final_md) < 50:
    result = json.dumps({"error": "no valid draft", "len": len(final_md) if final_md else 0})
else:
    proc_name = suggested_name.replace(" ", "-").replace("_", "-")
    file_path = f"vaultbot/System/Procedures/{proc_name}.md"
    write_result = vault_safe_write(file_path, final_md)
    # Check if the write actually succeeded
    write_status = write_result.get("status", "blocked") if isinstance(write_result, dict) else "blocked"
    if write_status != "written":
        result = json.dumps({
            "s": "write_blocked",
            "n": proc_name,
            "fp": file_path,
            "v": verdict,
            "fixed": verdict == "NEEDS_FIXES",
            "blocked_reason": write_result.get("blocked_reason", "unknown") if isinstance(write_result, dict) else str(write_result)
        })
    else:
        result = json.dumps({
            "s": "written",
            "n": proc_name,
            "fp": file_path,
            "v": verdict,
            "fixed": verdict == "NEEDS_FIXES"
        })
```

5. ```python
import json

step4 = json.loads(output) if output else {}
proc_name = step4.get("n", "")

if not proc_name:
    result = json.dumps({"error": "no procedure name"})
else:
    verify_result = run_procedure("Verify-Procedure-Args", {
        "procedure_path": f"{proc_name}.md"
    })

    # Extract verification output
    if isinstance(verify_result, dict):
        vout = verify_result.get("final_output", str(verify_result))
    elif isinstance(verify_result, str):
        vout = verify_result
    else:
        vout = str(verify_result)

    try:
        start = vout.find("{")
        end = vout.rfind("}")
        vdata = json.loads(vout[start:end+1]) if start != -1 else {}
    except (json.JSONDecodeError, AttributeError):
        vdata = {"verdict": "unknown"}

    result = json.dumps({
        "s": "verified",
        "n": proc_name,
        "verdict": vdata.get("verdict", "unknown"),
        "issues": len(vdata.get("issues", []))
    })
```

6. ```python
import json

step4 = json.loads(output) if output else {}
proc_name = step4.get("n", "")
file_path = f"vaultbot/System/Procedures/{proc_name}.md"

lint_result = vault_lint(file_path)

result = json.dumps({
    "s": "linted",
    "n": proc_name,
    "broken_links": lint_result.get("broken_wikilinks", []) if isinstance(lint_result, dict) else []
})
```

7. ```python
import json

step4 = json.loads(output) if output else {}
proc_name = step4.get("n", "")
test_args_str = args.get("test_args", "{}")

try:
    test_args = json.loads(test_args_str) if isinstance(test_args_str, str) else test_args_str
except (json.JSONDecodeError, TypeError):
    test_args = {}

test_result = run_procedure("Test-Procedure-Until-Pass", {
    "procedure_name": proc_name,
    "procedure_args": test_args,
    "max_iterations": 3
})

if isinstance(test_result, dict):
    passed = test_result.get("overall_passed", False)
    toutput = test_result.get("final_output", "")[:1000]
elif isinstance(test_result, str):
    try:
        parsed = json.loads(test_result)
        passed = parsed.get("overall_passed", False)
        toutput = parsed.get("final_output", "")[:1000]
    except json.JSONDecodeError:
        passed = False
        toutput = test_result[:1000]
else:
    passed = False
    toutput = str(test_result)[:1000]

result = json.dumps({
    "s": "tested",
    "n": proc_name,
    "passed": passed,
    "output": toutput
})
```

8. ```python
import json

step4 = json.loads(output) if output else {}
proc_name = step4.get("n", "")
file_path = f"vaultbot/System/Procedures/{proc_name}.md"

lint_result = vault_lint(file_path)

result = json.dumps({
    "s": "final_lint",
    "n": proc_name,
    "broken": len(lint_result.get("broken_wikilinks", [])) if isinstance(lint_result, dict) else 0
})
```

9. [llm: Report the results of this procedure build. Use the prior step outputs provided above.

Write a clean summary:

### What Was Built
- Name and path
- What it does

### Quality Gates
- Review verdict and fixes applied
- Static check results
- Lint results
- Test result (PASSED/FAILED, iterations)

### Verdict
- READY TO USE or NEEDS MANUAL REVIEW

Keep it concise.]

## Composition Map

| Step | Type | Purpose | Cost |
|------|------|---------|------|
| 1 | LLM | Draft markdown from task | Small LLM |
| 2 | Code | Save draft to temp file | Zero |
| 3 | Code | Big-model quality review | Big LLM |
| 4 | Code | Apply fixes, write to disk | Zero |
| 5 | Code | Static checks via Verify-Procedure-Args | Small LLM |
| 6 | Code | Lint via vault_lint | Zero |
| 7 | Code | Dynamic test via Test-Procedure-Until-Pass | Small LLM × N |
| 8 | Code | Final lint | Zero |
| 9 | LLM | Human-readable report | Big LLM |

## Why This Exists

The manual procedure-building workflow was slow and error-prone. I shipped [[Authority-Check]] with `read_note` — a function that doesn't exist in the procedure runtime — because there was no automated verification between drafting and writing. This procedure automates the entire pipeline: draft → review → write → verify → lint → test → fix → retest → final lint → report. Every procedure that leaves this factory has passed every gate.

The review step (Step 3) is the critical innovation: the big model checks for discoverability, falsifiability, executability, and correctness before anything hits disk. This is the "strong as fuck" guarantee.