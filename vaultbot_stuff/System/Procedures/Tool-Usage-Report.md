---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Find what the vaultbot does repeatedly in chat that could be turned into a procedure. Scans recent chat logs for recurring tool-call sequences (same tools in the same order, 3+ times) and returns the patterns with a suggested procedure name for each. Use when the vaultbot is doing the same thing manually over and over."
when_to_use: "when the vaultbot keeps doing the same multi-step workflow manually, when looking for what to proceduralize next, or when asked 'what should I automate'"
falsifiable_if: "the patterns don't actually recur 3+ times, or the suggested procedure names don't match what the pattern does"
applies_to:
  - self-improvement
  - procedure-discovery
  - automation
  - pattern-recognition
allowed_tools:
  - vault_list
  - llm_generate
---

# Tool-Usage-Report

## When to Run This

Run this when the vaultbot is doing the same things manually over and over.
It finds recurring tool-call sequences in chat history and suggests
procedures to automate them. This is how the procedure library grows
toward covering everything.

## Steps

### Step 1: Scan chat logs for tool-call sequences

1. ```python
import json, re
from collections import Counter

chat_dir = Path(vault_path) / "vaultbot_stuff" / "Memory" / "Chat"
if not chat_dir.exists():
    chat_dir = Path(vault_path) / "vaultbot_stuff" / "Memory"

# Collect tool-call sequences from chat logs
sequences = []
tool_counter = Counter()
for log_file in sorted(chat_dir.rglob("*.md"))[-30:]:  # last 30 chat logs
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    # Find tool call patterns (varies by log format)
    # Look for tool_name( or "tool": "name" patterns
    calls = re.findall(r'(?:execute_procedure|vault_search|code_read|vault_safe_write|'
                       r'vault_append|vault_research|web_read_source|vault_lint|'
                       r'vault_list|vault_delete|plan_task|update_task|'
                       r'safe_write|code_run|git_rollback|backend_restart)', text)
    for c in calls:
        tool_counter[c] += 1
    # Extract sequences of 2+ consecutive tool calls
    if len(calls) >= 2:
        for i in range(len(calls) - 1):
            seq = tuple(calls[i:i+3])  # 3-tool windows
            if len(seq) >= 2:
                sequences.append(seq)

# Count recurring sequences
seq_counter = Counter(sequences)
recurring = [{"sequence": list(s), "count": c} for s, c in seq_counter.most_common(15) if c >= 3]

result = json.dumps({
    "tool_frequency": dict(tool_counter.most_common(20)),
    "recurring_sequences": recurring,
    "logs_scanned": 30,
})
```

### Step 2: Small model suggests procedure names for recurring patterns

2. ```python
import json as _json

data = _json.loads(output)
recurring = data.get("recurring_sequences", [])
if not recurring:
    result = _json.dumps({"candidates": [], "note": "no recurring patterns found"})
else:
    prompt = f"""For each recurring tool-call sequence, suggest a procedure name
and what it would automate.

Recurring sequences:
{_json.dumps(recurring, indent=2)}

Return JSON: [{{"sequence": ["tool1", "tool2"], "count": N, "proposed_name": "Procedure-Name", "what_it_automates": "one sentence"}}]
Return ONLY the JSON array."""
    suggestions = llm_generate(prompt)
    result = suggestions
```

### Step 3: Return the procedure candidates

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"procedure_candidates": parsed,
                      "tool_frequency": data.get("tool_frequency", {})})
```