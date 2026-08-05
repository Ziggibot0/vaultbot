---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Find procedures that are never called by the vaultbot in chat. Scans chat history for execute_procedure calls, counts which procedures are used, and returns procedures with zero or very low usage. Use when auditing the procedure library for dead procedures.
when_to_use: when auditing the procedure library, when finding procedures that are never used, when cleaning up unused procedures, or when asked 'which procedures are never called'
falsifiable_if: the procedure flags a procedure as unused when it is called, or misses unused procedures
applies_to:
  - procedure-audit
  - procedure-library
  - self-improvement
  - cleanup
allowed_tools:
  - vault_list
  - llm_generate
summary: Find-Underused-Procedures
tags:
  - procedure
  - procedures
---

# Find-Underused-Procedures

## When to Run This

Run this to find procedures that exist but are never called. They might be
unnecessary, poorly discovered (bad description), or just not needed yet.

## Steps

### Step 1: Count procedure calls in chat history

1. ```python
import re, json
from collections import Counter

# Get all procedure names
proc_dir = Path(vault_path) / "vaultbot_stuff" / "System" / "Procedures"
all_procs = {p.stem for p in proc_dir.glob("*.md")}

# Scan chat logs for execute_procedure calls
chat_dir = Path(vault_path) / "vaultbot_stuff" / "Memory" / "Chat"
if not chat_dir.exists():
    chat_dir = Path(vault_path) / "vaultbot_stuff" / "Memory"

call_counter = Counter()
for log_file in sorted(chat_dir.rglob("*.md"))[-50:]:  # last 50 chat logs
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    # Find execute_procedure calls
    calls = re.findall(r'execute_procedure\s*\(\s*["\']([^"\']+)', text)
    for c in calls:
        call_counter[c] += 1
    # Also check for procedure names mentioned in tool-call context
    calls2 = re.findall(r'"procedure_name":\s*"([^"]+)"', text)
    for c in calls2:
        call_counter[c] += 1

# Find procedures with zero calls
unused = []
for proc in all_procs:
    count = call_counter.get(proc, 0)
    if count == 0:
        unused.append(proc)

# Get usage stats for used procedures
usage = [{"procedure": p, "calls": c} for p, c in call_counter.most_common(20) if p in all_procs]

result = json.dumps({
    "total_procedures": len(all_procs),
    "unused_procedures": sorted(unused),
    "unused_count": len(unused),
    "usage_stats": usage,
})
```

### Step 2: Small model categorizes why procedures are unused

2. ```python
import json as _json

data = _json.loads(output)
unused = data.get("unused_procedures", [])
if not unused:
    result = _json.dumps({"analysis": [], "note": "all procedures are used"})
else:
    # Read descriptions of unused procedures
    proc_dir = Path(vault_path) / "vaultbot_stuff" / "System" / "Procedures"
    proc_info = []
    for name in unused[:15]:
        p = proc_dir / f"{name}.md"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            desc = ""
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    for line in text[3:end].split("\n"):
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
            proc_info.append({"name": name, "description": desc[:120]})
        except Exception:
            continue

    prompt = f"""For each unused procedure, assess why it might be unused:
- POOR_DISCOVERY: the description isn't specific enough for RAG
- NICHE: it's for a rare task that just hasn't come up
- REDUNDANT: another procedure covers the same thing
- BROKEN: it probably doesn't work and was abandoned

Unused procedures:
{json.dumps(proc_info, indent=2)}

Return JSON: [{{"name": "...", "reason": "POOR_DISCOVERY|NICHE|REDUNDANT|BROKEN", "recommendation": "improve description|keep|merge|delete"}}]
Return ONLY the JSON array."""
    analysis = llm_generate(prompt)
    result = analysis
```

### Step 3: Return the audit

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({
    "unused_analysis": parsed,
    "total_unused": data.get("unused_count", 0),
    "usage_stats": data.get("usage_stats", []),
})
```