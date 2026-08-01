---
type: procedure
status: experimental
model_cartridge: small
created: 2026-07-31
description: "Extract atomic factual claims from a research note's synthesis section. Returns a JSON array of {claim, source} objects. Uses the small model — claim extraction is simple structured parsing."
when_to_use: "when you need to verify the factual claims in a research note (first step of Verify-Claims)"
falsifiable_if: "the extracted claims do not cover the key findings in the note, or claims are invented that aren't in the text"
applies_to:
  - claim-verification
  - research-quality
  - fact-checking
allowed_tools:
  - vault_search
  - llm_generate
---

# Extract-Claims

## When to Run This

Run this procedure when you need to verify the factual claims in a research note. It extracts atomic, verifiable claims from the note's synthesis section so each one can be checked against its cited source. This is the first step of the Verify-Claims workflow.

## Steps

### Step 1: Read the note and find the synthesis section

1. ```python
import re

note_path = vault_search(query=args.get("note_title", ""), k=1)
if not note_path:
    result = json.dumps({"error": "note not found"})
else:
    fp = note_path[0] if isinstance(note_path, list) else note_path
    text = Path(fp).read_text(encoding="utf-8", errors="replace") if isinstance(fp, str) else ""
    # Find the synthesis section
    synthesis_match = re.search(r'##\s*(?:Key Findings|Synthesis|Summary)\s*\n(.*?)(?:\n##\s|\Z)', text, re.DOTALL)
    synthesis_text = synthesis_match.group(1) if synthesis_match else text
    result = json.dumps({"note_path": str(fp), "synthesis_length": len(synthesis_text), "synthesis": synthesis_text[:3000]})
```

### Step 2: Extract claims using the small model

2. [llm: You are a claim extraction system. Extract all atomic factual claims from the following research text. Each claim should be a single verifiable sentence. Preserve any [sources: ...] citation. Return a JSON array of objects with 'claim' and 'source' fields. Text to extract from is in the prior step output. Return ONLY the JSON array.]

### Step 3: Return the extracted claims

3. ```python
# The LLM output from step 2 contains the claims as JSON
# Parse and return them
import json
try:
    claims = json.loads(output.strip())
    if not isinstance(claims, list):
        claims = []
except Exception:
    claims = []
result = json.dumps({"claims": claims, "count": len(claims)})
```