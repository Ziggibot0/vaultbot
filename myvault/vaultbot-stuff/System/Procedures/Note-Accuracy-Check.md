---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Verify a vault note's factual claims against the rest of the vault. Given a note path, extracts claims and searches the vault for supporting or contradicting evidence in other notes. Returns claims that are unsupported or contradicted by other vault notes. Use when validating a note before relying on it.
when_to_use: when validating a note's claims against vault knowledge, before relying on a note for a decision, or when asked 'is this note accurate'
falsifiable_if: the procedure flags a claim as unsupported when other vault notes do support it, or misses contradictions
applies_to:
  - claim-verification
  - vault-accuracy
  - fact-checking
  - vault-maintenance
allowed_tools:
  - vault_search
  - llm_generate
summary: Note-Accuracy-Check
tags:
  - procedure
  - procedures
---

# Note-Accuracy-Check

## When to Run This

Run this when you want to verify a note's claims against the rest of the
vault. Unlike checking against a single source, this searches the whole
vault for supporting or contradicting evidence.

## Why This Exists

A note's factual claims may be unsupported or contradicted by other vault
notes. This procedure extracts claims and searches the vault for supporting
or contradicting evidence. The tradeoff: it caps at 20 claims, so very
claim-dense notes are only partially checked.

## Steps

### Step 1: Read the note and extract claims

1. ```python
import json

note_path = args.get("note_path", "")
if not note_path:
    result = json.dumps({"error": "note_path argument required"})
else:
    p = Path(vault_path) / note_path
    if not p.exists():
        p = Path(note_path)
    if not p.exists():
        result = json.dumps({"error": f"note not found: {note_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Skip frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end+3:]
        prompt = f"""Extract the key factual claims from this note text. Return JSON: {{"claims": ["claim1", "claim2", ...]}}

Note text:
{text[:8000]}"""
```

### Step 2: Search the vault for evidence on each claim

2. ```python
import json

claims_raw = result if isinstance(result, str) else json.dumps(result)
try:
    claims_data = json.loads(claims_raw)
    claims = claims_data.get("claims", [])
except Exception:
    claims = []

search_results = {}
for claim in claims[:20]:  # cap at 20 claims
    # vault_search is called by the procedure runner for each claim
    search_results[claim] = f"SEARCH:{claim}"

result = json.dumps({"claims": claims, "search_queries": search_results})
```

### Step 3: Classify each claim's support level

3. [llm: Review the evidence for each claim. Classify each as:
- **supported**: other vault notes contain evidence consistent with the claim
- **contradicted**: other vault notes contain evidence that contradicts the claim
- **unsupported**: no relevant evidence found in the vault
- **self-evident**: the claim is a definition or tautology that doesn't need external support

Return JSON: {"results": [{"claim": "...", "verdict": "supported|contradicted|unsupported|self-evident", "notes": "..."}]}


## Related Procedures
- [[Cross-Check-Claims]] — verifies claims against web sources (external verification)
- [[vaultbot/Structure-Research-Note]] — parent procedure for note creation
- [[Vault-Lint]] — structural quality checks (broken links, frontmatter)