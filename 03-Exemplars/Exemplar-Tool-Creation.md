---
type: exemplar
exemplar: tool-creation
created: 2026-07-26
summary: "Annotated example of how to create a new tool — from capability audit through code, test, deploy, and verify. The model pattern-matches against this when asked to build a new tool."
tags: [exemplar, tool-creation, how-to, deterministic]
---

<!-- EXEMPLAR ANNOTATION: TOOL CREATION
     This note is an exemplar for creating new tools. When the model needs to
     build a tool, FUSED retrieval should surface this note. The model reads
     the annotated process and pattern-matches against it.

     The process is:
     1. Run capability_audit to check if a tool already exists
     2. If gap found, use self_reflect to propose a tool
     3. Write the tool code
     4. Test with code_run BEFORE deploying
     5. Deploy with tool_create
     6. Verify the tool works
     7. Run preflight_safety_check before any self-edit

     This exemplar uses vault_list (a real tool in this vault) as the worked example.
 -->

# Exemplar: Tool Creation

## Task

Create a tool that lists all `.md` files in the vault, optionally filtered by directory or tag.

## Step 1: Capability Audit

<!-- ANNOTATION: Always start with capability_audit. If a tool already exists, don't build a duplicate. -->
Run `capability_audit` with the task description. Result: no existing tool lists vault files. **Gap confirmed.**

## Step 2: Write the Tool Code

<!-- ANNOTATION: The tool must define a `run(args: dict) -> dict` function. The schema is added automatically by tool_create. Keep the interface simple — fewer parameters, descriptive descriptions. -->
```python
import os
import re

def run(args: dict) -> dict:
    vault_path = args.get("vault_path", ".")
    directory = args.get("directory", "")
    tag = args.get("tag", "")

    search_path = os.path.join(vault_path, directory) if directory else vault_path

    md_files = []
    for root, dirs, files in os.walk(search_path):
        for f in files:
            if f.endswith('.md'):
                rel_path = os.path.relpath(os.path.join(root, f), vault_path)
                md_files.append(rel_path)

    if tag:
        filtered = []
        for f in md_files:
            full_path = os.path.join(vault_path, f)
            try:
                with open(full_path, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                if f'#{tag}' in content:
                    filtered.append(f)
            except Exception:
                pass
        md_files = filtered

    return {"files": md_files, "count": len(md_files)}
```

## Step 3: Test with code_run

<!-- ANNOTATION: ALWAYS test with code_run before deploying. Never deploy untested code. If the test fails, fix the code and re-test. -->
```python
# Test code
import sys
sys.path.insert(0, 'custom_tools')
from vault_list import run
result = run({"vault_path": "."})
assert "files" in result
assert result["count"] > 0
print(f"Found {result['count']} files")
```

**Result:** Test passed. Found 154 files.

## Step 4: Deploy with tool_create

<!-- ANNOTATION: Deploy with tool_create. The tool is immediately callable by all MCP clients. Provide a clear description and parameter schema. -->
Call `tool_create` with:
- `tool_name`: "vault_list"
- `description`: "List all .md files in the vault. Optionally filter by directory or tag."
- `parameters`: JSON schema for vault_path, directory, tag
- `code`: The tested Python source

## Step 5: Verify

<!-- ANNOTATION: After deploying, verify the tool works by calling it. If it returns an error, use git_rollback to restore and try again. -->
Call `vault_list` with `{"vault_path": "."}`. Result: 154 files returned. **Tool verified.**

## Pattern Summary

<!-- ANNOTATION: End with a concise summary of the pattern. This is what the model pattern-matches against — the high-level shape of the process. -->
1. **Audit** — Check if the tool already exists
2. **Write** — Define `run(args: dict) -> dict`, keep interface simple
3. **Test** — Use `code_run` to verify before deploying
4. **Deploy** — Use `tool_create` with clear description + schema
5. **Verify** — Call the tool to confirm it works
6. **Never skip testing** — Untested code can break the backend

## Related
- [[Procedural-Bootstrap-and-Evolution-Plan]] — procedures + exemplars = scaffolding
- [[Small-Model-Path-to-AGI]] — why exemplars matter for 30B models
- [[Deterministic-Scaffolding-for-Small-Models]] — exemplars are deterministic scaffolding
- [[Exemplar-Note-Design]] — design principles for exemplar notes

## Why This Process Works

The test-before-deploy pattern matters because untested code can break the backend, which would make VaultBot unable to function. The capability audit first matters because building a duplicate tool wastes effort and creates confusion. The verify-after-deploy step matters because deployment can fail silently — the tool registers but doesn't execute correctly. Therefore, the full 5-step process is necessary, not optional.

LOCKED
