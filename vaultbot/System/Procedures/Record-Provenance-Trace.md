---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-17
description: "Record a provenance trace for a chat answer: which procedure produced it, what was retrieved, and the per-claim entailment verdicts. Appends a structured provenance block to the answer's chat note frontmatter so the reasoning chain from source to claim is permanently auditable. This is the 'auditable trace' layer of the provenance pillar."
when_to_use: after Verify-Answer-Entailment produces per-claim verdicts, to store them as a permanent, human-readable provenance manifest in the vault
falsifiable_if: the stored provenance block misattributes a claim to the wrong source, or the trace is incomplete (missing claims/sources)
applies_to:
  - provenance
  - auditability
  - claim-verification
  - chat-answer-verification
allowed_tools:
  - code_read
  - vault_safe_write
summary: Record-Provenance-Trace
tags:
  - procedure
  - procedures
  - provenance
---

# Record-Provenance-Trace

## When to Run This

Run this after [[Verify-Answer-Entailment]] produces per-claim verdicts, to store them as a permanent, human-readable provenance manifest. This turns "the answer cites a source" into "the answer cites a source, and here is the exact claim→source→verdict chain, stored in the vault."

## Design Principle

The provenance block is **structured frontmatter** (machine-checkable) but **human-readable** (a scholar can open the note and read the chain). It is the data structure every other verification layer runs on — without it, entailment verdicts are transient and un-auditable.

## Inputs

- `note_path` (string, required): Path to the chat note to annotate (relative to vault root).
- `verdicts` (string, required): JSON string of per-claim verdicts from Verify-Answer-Entailment.
- `procedure` (string, optional): The procedure that produced the answer (defaults to "chat").

## Outputs

- The updated note with a `provenance` frontmatter block appended.
- A confirmation string.

---

## Steps

### Step 1: Parse inputs and build the provenance block

1. ```python
import json

note_path = args.get("note_path", "")
verdicts_raw = args.get("verdicts", "[]")
procedure = args.get("procedure", "chat")

try:
    verdicts = json.loads(verdicts_raw)
except Exception:
    verdicts = []

# Build a compact, human-readable provenance block
lines = []
lines.append(f"procedure: {procedure}")
lines.append(f"verified_at: {__import__('datetime').datetime.now().isoformat()}")
lines.append(f"claim_count: {len(verdicts)}")
for v in verdicts:
    claim = (v.get("claim", "") or "")[:120]
    note = v.get("note", "")
    verdict = v.get("verdict", "unsupported")
    lines.append(f"- [{verdict}] {claim} -> [[{note}]]")

provenance_block = "\n".join(lines)
result = json.dumps({
    "note_path": note_path,
    "provenance_block": provenance_block,
    "claim_count": len(verdicts),
})
```

### Step 2: Append the provenance block to the note's frontmatter

2. ```python
import json, re

data = json.loads(output)
note_path = data.get("note_path", "")
provenance_block = data.get("provenance_block", "")

if not note_path or not provenance_block:
    result = json.dumps({"error": "note_path and provenance_block required"})
else:
    # code_read resolves relative paths against the subprocess cwd, so
    # resolve note_path against the vault root first.
    from pathlib import Path as _P
    _abs = _P(note_path)
    if not _abs.is_absolute():
        _abs = _P(vault_path) / note_path
    try:
        content = code_read(str(_abs))
        if isinstance(content, dict):
            text = content.get("content", "") or content.get("text", "")
        else:
            text = str(content)
    except Exception:
        text = ""

    # Insert the provenance block into frontmatter (or create frontmatter)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            # Append to existing frontmatter before the closing ---
            new_text = text[:end] + "\nprovenance:\n" + "\n".join(
                "  " + line for line in provenance_block.split("\n")
            ) + text[end:]
        else:
            new_text = text
    else:
        new_text = "---\nprovenance:\n" + "\n".join(
            "  " + line for line in provenance_block.split("\n")
        ) + "\n---\n\n" + text

    try:
        vault_safe_write(note_path, new_text)
        result = json.dumps({"status": "ok", "note_path": note_path})
    except Exception as e:
        result = json.dumps({"error": str(e)})
```

## Related

- [[Verify-Answer-Entailment]] — produces the per-claim verdicts this procedure stores
- [[Check-Entailment]] — the single-pair entailment primitive
- [[Provenance-Audit]] — audits a note for missing provenance (the structural check)
- [[Cite-Provenance]] — forces every claim to cite a vault note (the citation-enforcement step)
