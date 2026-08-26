---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Write a new procedure note from a spec. Given a task description, the small model drafts a complete procedure note with frontmatter (type, status, model_cartridge, description, when_to_use, allowed_tools) and step structure. Returns the full markdown for review. Does NOT write it to disk — the big model reviews and writes it with vault_safe_write.
when_to_use: when you need to create a new procedure and want a draft to review, when Discover-Procedures or Tool-Usage-Report identified a candidate, or when asked to 'make a procedure for X'
falsifiable_if: the drafted procedure has invalid frontmatter, non-executable steps, or doesn't match the requested task
applies_to:
  - procedure-creation
  - self-improvement
  - procedure-library
  - drafting
allowed_tools:
  - vault_list
  - llm_generate
summary: Write-Procedure-Draft
tags:
  - procedure
  - procedures
---

# Write-Procedure-Draft

## When to Run This

When you need a new procedure, run this to get a complete draft. The small
model writes the frontmatter and step structure. The big model reviews it
and writes it to disk with `vault_safe_write`.

## Why This Exists

Creating a new procedure needed a draft to review before committing it to the library. This procedure exists to have the small model draft a complete procedure note from a task description. The key tradeoff: it does NOT write to disk — it returns the markdown for the big model to review and write, so a bad draft can't silently enter the library.

## Steps

### Step 1: List existing procedures for context (avoid duplicates)

1. ```python
import json

proc_dir = Path(vault_path) / "vaultbot-stuff" / "System" / "Procedures"
existing = [p.stem for p in proc_dir.glob("*.md")]
result = json.dumps({"existing_procedures": existing})
```

### Step 2: Small model drafts the procedure

2. ```python
import json as _json

task = args.get("task", "")
tools_available = args.get("tools", "vault_search, code_read, llm_generate, vault_list, run_procedure")
data = _json.loads(output)
existing = data.get("existing_procedures", [])

if not task:
    result = _json.dumps({"error": "task argument required"})
else:
    prompt = f"""Write a complete procedure note for this task:

Task: {task}

Existing procedures (avoid duplicates): {existing}

Available tools that can be listed in allowed_tools:
llm_generate, vault_search, web_read_source, vault_lint, vault_append,
vault_list, code_read, run_procedure, vault_graph_analyzer, vault_delete

Rules:
- model_cartridge: small for classification, extraction, routing, formatting
- model_cartridge: big only for novel reasoning or complex synthesis
- Code steps use ```python blocks and set `result = json.dumps(...)`
- LLM steps use [llm: instruction] format
- EVERY step MUST have a `### Step N: short-summary` header (e.g. `### Step 1: Search the vault`).
  Put the ```python fence or [llm: ...] tag on the line(s) AFTER the header.
  The header summary is the step's human-readable description. NEVER use bare `N.` without a header.
- description must be specific enough that RAG surfaces it for the right intent
- when_to_use must describe the situation, not the topic
- Keep code steps deterministic and idempotent

Write the FULL markdown including frontmatter. Return ONLY the markdown."""
    draft = llm_generate(prompt)
    result = draft
```

### Step 3: Return the draft

3. ```python
# The draft is the LLM output from step 2
result = output
```

## Related

- [[Create-New-Procedure]] — creates a new procedure
- [[Tool-Usage-Report]] — identifies procedure candidates to draft
- [[Write-Note]] — writes the drafted note to disk