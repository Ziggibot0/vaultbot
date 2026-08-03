---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Find pairs of vault notes that explicitly contradict each other — one says X is true, another says X is false. Scans notes for assertive statements, then the small model checks pairs for direct contradictions. Returns the contradicting pairs with the specific conflicting claims. Use when the vault has grown organically and may contain conflicting information."
when_to_use: "when looking for direct contradictions between notes, when the vault has conflicting information, when reconciling knowledge, or when asked 'do any notes contradict each other'"
falsifiable_if: "the procedure reports contradictions that aren't real, or misses real contradictions"
applies_to:
  - contradiction-detection
  - vault-quality
  - reconciliation
  - vault-maintenance
allowed_tools:
  - vault_list
  - llm_generate
---

# Find-Vault-Contradictions

## When to Run This

Run this to find direct contradictions between vault notes — note A says
something is true, note B says it's false. This is the vault-wide version
of single-concept comparison — it scans all notes for contradictions.

## Steps

### Step 1: Extract assertive statements from all notes

1. ```python
import re, json

all_files = vault_list()
statements = []
for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    if "/Procedures/" in rel or "/Build-Log/" in rel or "/Chat/" in rel:
        continue
    # Extract sentences that make assertions
    sentences = re.split(r'[.\n]+', text)
    for s in sentences:
        s = s.strip()
        if len(s) > 30 and len(s) < 200:
            # Look for assertive language
            if re.search(r'\b(is|are|was|were|must|should|always|never|cannot|requires)\b', s, re.IGNORECASE):
                statements.append({"note": rel, "statement": s})

# Limit to keep context manageable
statements = statements[:100]
result = json.dumps({"statements": statements, "total": len(statements)})
```

### Step 2: Small model finds contradictions

2. ```python
import json as _json

data = _json.loads(output)
statements = data.get("statements", [])
if len(statements) < 2:
    result = _json.dumps({"contradictions": [], "note": "not enough statements to compare"})
else:
    # Batch the statements for the model
    stmt_text = "\n".join(f"[{i}] {s['note']}: {s['statement']}" for i, s in enumerate(statements))
    prompt = f"""Find pairs of statements that directly contradict each other.
One says X is true, the other says X is false. Only flag DIRECT contradictions,
not just different perspectives or complementary information.

Statements:
{stmt_text}

Return JSON: [{{"statement_a_idx": N, "statement_b_idx": N, "topic": "what they disagree about", "contradiction": "how they contradict"}}]
Return ONLY the JSON array."""
    contradictions_raw = llm_generate(prompt)
    try:
        start = contradictions_raw.find("[")
        end = contradictions_raw.rfind("]")
        pairs = json.loads(contradictions_raw[start:end+1]) if start != -1 else []
    except Exception:
        pairs = []

    contradictions = []
    for pair in pairs:
        a_idx = pair.get("statement_a_idx", -1)
        b_idx = pair.get("statement_b_idx", -1)
        if 0 <= a_idx < len(statements) and 0 <= b_idx < len(statements):
            contradictions.append({
                "note_a": statements[a_idx]["note"],
                "claim_a": statements[a_idx]["statement"],
                "note_b": statements[b_idx]["note"],
                "claim_b": statements[b_idx]["statement"],
                "topic": pair.get("topic", ""),
                "contradiction": pair.get("contradiction", ""),
            })
    result = _json.dumps({"contradictions": contradictions,
                          "statements_checked": len(statements)})
```

### Step 3: Return the contradictions

3. ```python
import json as _json

data = _json.loads(output)
contradictions = data.get("contradictions", [])
result = _json.dumps({
    "contradiction_count": len(contradictions),
    "contradictions": contradictions,
    "statements_checked": data.get("statements_checked", 0),
})
```