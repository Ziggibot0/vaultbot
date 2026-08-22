---
type: procedure
status: active
baseline: true
model_cartridge: big
created: 2026-08-22
description: "Prove a code change against official docs before writing it. Reads the current file, identifies external APIs, fetches their authoritative docs, verifies the change against them, then calls safe_write with the doc_source attached. This is the single correct path for any backend edit — safe_write rejects edits without a doc_source."
when_to_use: "Before ANY safe_write that imports a stdlib or third-party module. This is the default (and only) path for editing backend code that touches external APIs."
falsifiable_if: "it writes a change whose external API usage contradicts the official docs, or reports success without attaching a doc_source"
applies_to:
  - code-verification
  - anti-hallucination
  - provenance
  - self-modification
  - orchestration
allowed_tools:
  - code_read
  - run_procedure
  - safe_write
summary: Prove-Code-Change
tags:
  - procedure
  - procedures
  - code-verification
  - anti-hallucination
  - orchestration
---

# Prove-Code-Change

## Purpose

The single correct path for editing backend code that touches external
APIs. It chains: read the current file → identify external APIs → fetch
their official docs → verify the change against them → attach the
`doc_source` → call `safe_write`. `safe_write` rejects any edit that
imports a non-VaultBot module without a `doc_source`, so this procedure
is not optional — it is the only way such an edit lands.

## Why This Exists

`safe_write` verifies code *syntactically* but never *semantically*. This
procedure closes that gap by proving the change against real documentation
before the write, making "write from model weights" impossible: the write
tool refuses unproven edits, and this procedure is the path of least
resistance to satisfy it.

## Inputs

- `file_path` (string, required): the file to edit (relative to vault root).
- `new_content` (string, required): the proposed new file content.
- `dry_run` (boolean, optional): preview the write without committing.

## Output Contract

Returns the `safe_write` result (status "written" | "dry_run_ok" |
"rejected") with the `doc_source` that was attached.

---

## Steps

### Step 1: Read the current file

1. ```python
import json

file_path = args.get("file_path", "")
if not file_path:
    print(json.dumps({"error": "file_path argument required"}))
    exit(1)

r = code_read(file_path)
print(json.dumps({"file_path": file_path, "current": r}))
```

### Step 2: Verify the change against official docs

2. ```python
import json

file_path = args.get("file_path", "")
new_content = args.get("new_content", "")

# Delegate the doc-verification to Check-API-Against-Docs.
result = run_procedure("Check-API-Against-Docs", {
    "file_path": file_path,
    "new_content": new_content,
})
print(json.dumps(result, default=str))
```

### Step 3: Extract the doc_source and verdict

3. [llm: Read the Check-API-Against-Docs result from Step 2. Extract:

   - `doc_source`: the list of official-docs URLs (from the step-3 output
     of that procedure, the "doc_sources" field).
   - `verdict`: "verified" | "hallucinated" | "unverified" (from step 4).

   If the verdict is "hallucinated", STOP — do not write. Report which
   module/API is wrong and why, and fix the change before retrying.

   If the verdict is "unverified" (docs missing/inconclusive), do NOT
   guess — either fetch the docs again or mark the change as needing
   manual review. Do not write unverified external-API code.

   If the verdict is "verified", proceed to Step 4 with the doc_source
   list. Output JSON: {"verdict": ..., "doc_source": [...]}]

### Step 4: Write the change with the doc_source attached

4. ```python
import json

file_path = args.get("file_path", "")
new_content = args.get("new_content", "")
dry_run = bool(args.get("dry_run", False))

# The doc_source list comes from Step 3's output (the LLM step).
# The runtime exposes it via `output`; parse defensively.
doc_source = None
try:
    parsed = json.loads(output) if isinstance(output, str) else output
    ds = parsed.get("doc_source")
    if isinstance(ds, list) and ds:
        doc_source = "; ".join(ds)
    elif isinstance(ds, str) and ds:
        doc_source = ds
except Exception:
    doc_source = None

if not doc_source:
    print(json.dumps({
        "error": "no doc_source produced — the change was not proven against docs",
        "hint": "re-run Check-API-Against-Docs and confirm a verified verdict",
    }))
    exit(1)

result = safe_write(
    file_path=file_path,
    content=new_content,
    dry_run=dry_run,
    doc_source=doc_source,
)
print(json.dumps(result, default=str))
```

### Step 5: Confirm the write and report provenance

5. [llm: Read the safe_write result from Step 4. If status is "written" or
   "dry_run_ok", report success with the doc_source that was attached. If
   status is "rejected", read the error and hint — the rejection is either
   a doc-source failure (re-run this procedure) or a syntactic/import
   failure (fix the code). Do NOT bypass the gate by switching to code_write
   or code_run for file modification — that is the wrong path and is
   forbidden.]

## Related

- [[Check-API-Against-Docs]] — the doc-verification sub-procedure this orchestrates
- [[Safe-Write]] — the write tool that requires the doc_source
- [[Choose-Write-Tool]] — route to the correct write tool first
- [[Verify-Backend-Change]] — the post-write test/restart/health chain
- [[Code-Audit-Senior-Review]] — static quality checks after the write
