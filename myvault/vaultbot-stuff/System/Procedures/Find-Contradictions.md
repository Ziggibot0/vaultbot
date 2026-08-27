---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Find vault notes that contradict what the backend code actually does. Walks the System/ and Knowledge/ notes that describe backend behavior, reads the actual .py source, and asks the small model whether the note's description matches the code. Returns a list of contradictions (note path, claim, code location, mismatch). Records and history notes are fine — only flag notes that describe current behavior incorrectly.
when_to_use: when you suspect vault notes are out of sync with the code, after a backend refactor, before trusting a vault note that explains how something works, or when asked 'do the docs match the code'
falsifiable_if: a flagged contradiction is actually correct (the note and code agree), or a real contradiction is missed
applies_to:
  - vault-code-sync
  - documentation-accuracy
  - contradiction-detection
  - vault-maintenance
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
summary: Find-Contradictions
tags:
  - procedure
  - procedures
---

# Find-Contradictions

## When to Run This

Run this when you need to verify that vault notes describing backend behavior
are actually accurate. After refactors, notes go stale. This procedure catches
notes that explain things *wrong* — not missing notes, not historical records,
just notes whose description of current behavior contradicts the code.

## Why This Exists

After a backend refactor, vault notes that describe how the code works go stale and start explaining things wrong, but the mismatch is invisible until someone compares note to code. This procedure exists to read the actual `.py` source and flag notes whose description contradicts it. The key tradeoff is that it only flags notes describing *current* behavior — historical records and history notes are explicitly ignored.

## What It Does

1. Find notes in `System/` and `Knowledge/` that reference `.py` files or
   describe backend mechanics.
2. For each note, extract the sentences that describe how something works.
3. Read the actual code file referenced.
4. Small model checks: does the note's description match what the code does?
5. Return only the contradictions with the note path, the claim, and the code
   location that contradicts it.

## Steps

### Step 1: Collect candidate notes that describe backend behavior

1. ```python
import re, json

candidates = []
for fp in vault_list():
    p = Path(fp)
    # Only System and Knowledge notes, skip procedures and exemplars
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    if not (rel.startswith("System/") or rel.startswith("Knowledge/")):
        continue
    if "/Procedures/" in rel or "/Exemplars/" in rel:
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    # Look for references to .py files or backend mechanics
    py_refs = re.findall(r'[\w_]+\.py', text)
    mech_keywords = ["backend", "chat_handler", "procedure", "tool", "rag",
                     "retrieval", "embedding", "indexer", "ollama", "model",
                     "cartridge", "agent", "loop", "consolidation", "amem",
                     "checkpointer", "self_improver", "custom_tools"]
    has_mech = any(kw in text.lower() for kw in mech_keywords)
    if py_refs or has_mech:
        candidates.append({"path": rel, "py_refs": list(set(py_refs))[:5],
                           "snippet": text[:1200]})

result = json.dumps({"candidates": candidates[:25], "total": len(candidates)})
```

### Step 2: For each candidate, check note claims against actual code

2. ```python
import json as _json

cands = _json.loads(output)
candidates = cands.get("candidates", [])
contradictions = []

for c in candidates:
    note_snippet = c["snippet"]
    py_refs = c["py_refs"]
    if not py_refs:
        continue
    # Read the first referenced .py file
    backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
    code_snippets = []
    for pyf in py_refs[:2]:
        code_path = backend_dir / pyf
        if not code_path.exists():
            # Try to find it
            matches = list(backend_dir.rglob(pyf))
            if matches:
                code_path = matches[0]
            else:
                continue
        try:
            code_text = code_path.read_text(encoding="utf-8", errors="replace")
            code_snippets.append({"file": pyf, "content": code_text[:1500]})
        except Exception:
            continue
    if not code_snippets:
        continue
    code_ctx = _json.dumps(code_snippets)
    prompt = f"""You are a documentation-code consistency checker.
Note file: {c['path']}
Note content (first 1200 chars):
{note_snippet}

Actual code:
{code_ctx}

Does the note's description of backend behavior MATCH what the code does?
Ignore history, records, and past states. Only flag if the note describes
CURRENT behavior and it is WRONG.
Return JSON: {{"match": true/false, "contradiction": "what is wrong", "note_claim": "the claim", "code_truth": "what code actually does"}}
If the note is a historical record or doesn't describe behavior, return match:true.
Return ONLY the JSON."""

    verdict = llm_generate(prompt)
    try:
        start = verdict.find("{")
        end = verdict.rfind("}")
        if start != -1 and end > start:
            parsed = _json.loads(verdict[start:end+1])
            if not parsed.get("match", True):
                contradictions.append({
                    "note": c["path"],
                    "contradiction": parsed.get("contradiction", ""),
                    "note_claim": parsed.get("note_claim", ""),
                    "code_truth": parsed.get("code_truth", ""),
                })
    except Exception:
        continue

result = _json.dumps({"contradictions": contradictions,
                      "checked": len(candidates),
                      "contradiction_count": len(contradictions)})
```

### Step 3: Return the contradiction report

3. [llm: Format the contradictions from the prior step as a clear report. For each contradiction, show: the note path, what the note claims, what the code actually does, and a suggested fix (update the note to match the code, or update the code to match the note). If there are no contradictions, say the vault notes are in sync with the code.]

## Related

- [[Find-Dead-Code]] — sibling code-quality probe
- [[Note-vs-Code-Diff]] — compares notes against code
- [[Find-Vault-Contradictions]] — sibling contradiction detection