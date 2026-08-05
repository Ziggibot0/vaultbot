---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Check if procedure descriptions still match what they actually do, and optionally fix them. In detect mode (default), reads each procedure's frontmatter description and steps, then the small model verifies accuracy. In optimize mode, also writes improved descriptions directly to the procedure notes' frontmatter. Absorbs the former Procedure-Description-Optimizer. Use when procedure descriptions feel stale or need updating.
when_to_use: when a procedure's description doesn't match what it does, when procedure descriptions are stale after edits, when auditing procedure quality, before relying on a procedure's description for discovery, or when you want to auto-fix stale descriptions
falsifiable_if: the procedure reports a mismatch that doesn't exist, misses a real description-step mismatch, or writes a description that's worse than the original
applies_to:
  - procedure-quality
  - description-accuracy
  - procedure-audit
  - vault-maintenance
allowed_tools:
  - vault_list
  - llm_generate
summary: "## Summary"
tags:
  - procedure
  - procedures
---

# Check-Procedure-Drift

## When to Run This

Procedure descriptions are what RAG uses for discovery. If the description
doesn't match what the procedure actually does, RAG will surface it for the
wrong intent. Run this to catch description-step drift and optionally fix it.

**Modes:**
- `detect` (default): Report mismatches without changing anything.
- `optimize`: Report mismatches AND write improved descriptions to the
  procedure notes' frontmatter. This absorbs the former Procedure-Description-Optimizer.

## Steps

### Step 1: Read all procedures' descriptions and steps

1. ```python
import json, re

proc_dir = Path(vault_path) / "vaultbot_stuff" / "System" / "Procedures"
procedures = []
for p in proc_dir.glob("*.md"):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if not text.startswith("---"):
        continue
    end = text.find("---", 3)
    if end == -1:
        continue
    fm = text[3:end]
    body = text[end+3:]
    desc = ""
    when = ""
    for line in fm.split("\n"):
        if line.strip().startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
        if line.strip().startswith("when_to_use:") or line.strip().startswith("when:"):
            when = line.split(":", 1)[1].strip().strip('"').strip("'")
    # Extract step text (rough)
    steps = re.findall(r'^\d+\.\s+(.+?)(?=\n\d+\.|\Z)', body, re.MULTILINE | re.DOTALL)
    procedures.append({"name": p.stem, "path": str(p), "description": desc[:200],
                       "when_to_use": when[:200],
                       "steps_preview": [s[:200] for s in steps[:5]],
                       "raw_fm": fm, "raw_text": text})

result = json.dumps({"procedures": procedures, "count": len(procedures)})
```

### Step 2: Small model checks each procedure's description vs steps

2. ```python
import json as _json

data = _json.loads(output)
procedures = data.get("procedures", [])
mode = args.get("mode", "detect")
mismatches = []

for proc in procedures:
    prompt = f"""Does this procedure's description accurately describe what
its steps actually do?

Procedure: {proc['name']}
Description: {proc['description']}
When to use: {proc['when_to_use']}
Steps:
{json.dumps(proc['steps_preview'], indent=2)}

Return JSON: {{"matches": true/false, "issue": "what's wrong if any", "suggested_description": "better description if wrong", "suggested_when": "better when_to_use if wrong"}}
Return ONLY the JSON."""
    check = llm_generate(prompt)
    try:
        start = check.find("{")
        end = check.rfind("}")
        parsed = _json.loads(check[start:end+1])
        if not parsed.get("matches", True):
            mismatches.append({"procedure": proc["name"],
                               "path": proc["path"],
                               "issue": parsed.get("issue", ""),
                               "suggested_description": parsed.get("suggested_description", ""),
                               "suggested_when": parsed.get("suggested_when", ""),
                               "raw_text": proc["raw_text"]})
    except Exception:
        pass

result = _json.dumps({"mismatches": mismatches, "mode": mode, "total_checked": len(procedures)})
```

### Step 3: In optimize mode, write improved descriptions to frontmatter

3. ```python
import json as _json, re

data = _json.loads(output)
mismatches = data.get("mismatches", [])
mode = data.get("mode", "detect")

if mode != "optimize" or not mismatches:
    result = _json.dumps({"mismatches": mismatches, "mode": mode,
                          "optimized": 0, "note": "detect mode or no mismatches"})
else:
    optimized = 0
    for m in mismatches:
        path = m["path"]
        try:
            text = m["raw_text"]
            # Replace description in frontmatter
            new_desc = m["suggested_description"]
            new_when = m.get("suggested_when", "")
            if new_desc:
                text = re.sub(r'(description:\s*)"[^"]*"',
                              f'\\1"{new_desc}"', text, count=1)
            if new_when:
                text = re.sub(r'(when_to_use:\s*)"[^"]*"',
                              f'\\1"{new_when}"', text, count=1)
            Path(path).write_text(text, encoding="utf-8")
            optimized += 1
        except Exception as e:
            pass
    result = _json.dumps({"mismatches": mismatches, "mode": mode,
                          "optimized": optimized})
```