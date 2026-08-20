---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-09
description: "Behavioral-Pattern-Mine scans all session JSONL logs for recurring VaultBot tool-call sequences (n-grams of length 2-5) that appear across 3+ sessions and are NOT already covered by existing procedures. It mines tool_call_requested, custom_tool_executed, and tool_exec_enter events -- the actual VaultBot tool calls, not backend plumbing. Surfaces automation candidates that should be consolidated into procedures."
when_to_use: "During Dream-Pass, after chat consolidation, to detect manual patterns that should be automated. Also runnable standalone to audit for automation gaps."
falsifiable_if: "It misses a recurring sequence that appears in 3+ sessions, or suggests a pattern already covered by an existing procedure."
applies_to:
  - self-knowledge
  - pattern-analysis
  - automation
  - procedure-creation
allowed_tools:
  - vault_list
  - vault_read_note
  - code_read
summary: |
  Behavioral-Pattern-Mine scans session JSONL logs for recurring VaultBot tool-call sequences (n-grams of length 2-5) that appear across 3+ sessions and are NOT already covered by existing procedures.
  1. List all session JSONL files under `vaultbot/vaultbot_backend/sessions`.
  2. For each session, extract ordered tool-call sequences from tool_call_requested, custom_tool_executed, and tool_exec_enter events.
  3. Build n-gram frequency counts (lengths 2-5) across all sessions.
  4. List all existing procedure names from `vaultbot/System/Procedures/`.
  5. Filter out sequences already covered by existing procedures.
  6. Rank remaining candidates by frequency x sequence length.
  7. Write a JSON report to `vaultbot/Memory/Build-Log/behavioral-pattern-mine.json`.
  8. Print a concise summary of top automation candidates.
tags:
  - procedure
  - self-knowledge
  - pattern-analysis
  - automation
---

# Behavioral-Pattern-Mine

## Purpose

Scans all session JSONL logs for recurring tool-call sequences that VaultBot performs manually across multiple sessions. These are automation candidates -- patterns that should be consolidated into a procedure so the big model doesn't have to re-derive the same workflow every time.

**Data source:** `tool_call_requested`, `custom_tool_executed`, and `tool_exec_enter` events in the session JSONL logs. These are VaultBot's actual tool calls (code_read, safe_replace, execute_procedure, etc.) -- NOT the backend plumbing events (ollama, vault_indexer) which are logged under the `tool_call` event type.

Unlike [[Session-Effort-Analysis]] (which counts token consumption and bigram frequencies), this procedure mines for **behavioral sequences** of length 2-5 that appear in 3+ distinct sessions. A sequence like `code_read -> safe_replace -> backend_restart` appearing 13 times across different sessions is a strong signal that this workflow should be a procedure.

## Inputs

No explicit arguments. The procedure scans `vaultbot/vaultbot_backend/sessions/` and `vaultbot/System/Procedures/`.

## Output Contract

**File written:** `vaultbot/Memory/Build-Log/behavioral-pattern-mine.json`

Human-readable summary is printed as the final output.

---

## Steps

### Step 1: Mine recurring tool-call sequences from session logs

1. ```python
import json, os
from pathlib import Path
from collections import defaultdict

# Resolve vault root (use injected vault_path from wrapper)
vault_root = Path(vault_path)
sessions_dir = vault_root / "vaultbot" / "vaultbot_backend" / "sessions"
proc_dir = vault_root / "vaultbot" / "System" / "Procedures"
output_dir = vault_root / "vaultbot" / "Memory" / "Build-Log"
output_dir.mkdir(parents=True, exist_ok=True)
out_file = output_dir / "behavioral-pattern-mine.json"

# Step 1: List all session JSONL files
session_files = list(sessions_dir.rglob("*.jsonl"))
if not session_files:
    raise RuntimeError("No session logs found in vaultbot/vaultbot_backend/sessions.")

# Step 2: Extract ordered tool-call sequences from each session JSONL
# We mine THREE event types that represent VaultBot's actual tool calls:
#   - tool_call_requested: {"event": "tool_call_requested", "data": {"tool": "code_read", ...}}
#   - custom_tool_executed: {"event": "custom_tool_executed", "data": {"name": "thought", ...}}
#   - tool_exec_enter: {"event": "tool_exec_enter", "data": {"tool": "code_read", ...}}
# We do NOT mine "tool_call" events -- those are backend plumbing (ollama, vault_indexer).
session_sequences = []  # list of (session_id, [tool_names])
for sf in session_files:
    tools = []
    for line in sf.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = obj.get("event", "")
        tool = None
        if ev == "tool_call_requested":
            tool = obj.get("data", {}).get("tool", "")
        elif ev == "custom_tool_executed":
            tool = obj.get("data", {}).get("name", "")
        elif ev == "tool_exec_enter":
            tool = obj.get("data", {}).get("tool", "")
        if tool:
            tools.append(tool)
    if tools:
        session_id = sf.stem
        session_sequences.append((session_id, tools))

print(f"Scanned {len(session_files)} session logs, found {len(session_sequences)} with VaultBot tool calls.")

# Step 3: Build n-gram frequency counts (lengths 2-5) across sessions
# Count each n-gram once per session (don't double-count within a session)
ngram_session_counts = defaultdict(set)  # ngram_tuple -> set of session_ids

for session_id, tools in session_sequences:
    for n in range(2, 6):  # lengths 2 through 5
        for i in range(len(tools) - n + 1):
            ngram = tuple(tools[i:i+n])
            ngram_session_counts[ngram].add(session_id)

# Convert to frequency: how many distinct sessions each n-gram appears in
ngram_freq = {ngram: len(sessions) for ngram, sessions in ngram_session_counts.items()}

# Filter: only keep n-grams that appear in 3+ distinct sessions
candidates = {ngram: freq for ngram, freq in ngram_freq.items() if freq >= 3}

print(f"Found {len(candidates)} n-gram patterns appearing in 3+ sessions.")

# Step 4: List all existing procedure names
existing_procedures = set()
if proc_dir.exists():
    for proc_file in proc_dir.rglob("*.md"):
        existing_procedures.add(proc_file.stem)

print(f"Found {len(existing_procedures)} existing procedures.")

# Step 5: Filter out sequences already covered by existing procedures
def is_covered(ngram, existing_procs):
    """Check if an n-gram is likely already covered by an existing procedure."""
    for proc in existing_procs:
        proc_lower = proc.lower().replace("-", " ")
        matches = sum(1 for tool in ngram if tool.lower().replace("_", " ") in proc_lower)
        if matches >= len(ngram) * 0.5:  # 50%+ tool overlap
            return True, proc
    return False, None

uncovered = {}
for ngram, freq in candidates.items():
    covered, proc = is_covered(ngram, existing_procedures)
    if not covered:
        uncovered[ngram] = freq

print(f"After filtering: {len(uncovered)} uncovered patterns.")

# Step 6: Rank by frequency x sequence length
ranked = sorted(uncovered.items(), key=lambda x: x[1] * len(x[0]), reverse=True)

# Build report
report = {
    "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    "total_session_files": len(session_files),
    "sessions_with_tool_calls": len(session_sequences),
    "total_patterns_found": len(candidates),
    "uncovered_patterns": len(uncovered),
    "existing_procedures_count": len(existing_procedures),
    "top_candidates": [
        {
            "sequence": " -> ".join(ngram),
            "length": len(ngram),
            "session_count": freq,
            "priority_score": freq * len(ngram),
            "suggested_procedure_name": "-".join(t.replace("_", "-") for t in ngram),
        }
        for ngram, freq in ranked[:15]
    ],
}

# Write JSON report
out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

# Print summary
print(f"\n**Behavioral-Pattern-Mine Report (generated {report['generated_at']})**\n"
      f"Session logs scanned: {report['total_session_files']}\n"
      f"Sessions with VaultBot tool calls: {report['sessions_with_tool_calls']}\n"
      f"Total n-gram patterns (3+ sessions): {report['total_patterns_found']}\n"
      f"Uncovered (not yet a procedure): {report['uncovered_patterns']}\n"
      f"\n**Top Automation Candidates:**\n")

for i, cand in enumerate(report["top_candidates"][:10], 1):
    print(f"  {i}. {cand['sequence']} "
          f"(length={cand['length']}, sessions={cand['session_count']}, "
          f"priority={cand['priority_score']})")
    print(f"     -> Suggested procedure: {cand['suggested_procedure_name']}")

print(f"\nFull JSON report written to {out_file}")
result = json.dumps(report)
```

[validate: at_least 1 candidates in report["top_candidates"] OR report["total_patterns_found"] == 0]

### Step 2: Extract stress signals from session logs

Stress signals are emitted at end-of-turn by chat_handler.py. Each one contains the user's message (intent), the tools used, findings, token cost, and flags for manual work vs procedure calls. These are the "pain" signals Dream Pass should heal with new procedures.

```python
import json
from pathlib import Path
from collections import defaultdict

vault_root = Path(vault_path)
sessions_dir = vault_root / "vaultbot" / "vaultbot_backend" / "sessions"

# Extract all stress_signal events across sessions
stress_signals = []
for sf in sessions_dir.rglob("*.jsonl"):
    for line in sf.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "stress_signal":
            data = obj.get("data", {})
            stress_signals.append({
                "session_id": obj.get("session_id", ""),
                "user_message": data.get("user_message", ""),
                "tools_used": data.get("tools_used", []),
                "tool_count": data.get("tool_count", 0),
                "rounds": data.get("rounds", 0),
                "findings": data.get("findings", []),
                "total_tokens": data.get("total_tokens", 0),
                "failed_writes": data.get("failed_writes", 0),
                "answer_length": data.get("answer_length", 0),
                "had_procedure_calls": data.get("had_procedure_calls", False),
                "had_manual_work": data.get("had_manual_work", False),
            })

# Filter: only keep turns with manual work (the pain signals)
manual_signals = [s for s in stress_signals if s["had_manual_work"]]
print(f"Found {len(stress_signals)} stress signals total, {len(manual_signals)} with manual work.")

# Group by similar user_message (first 100 chars as key)
intent_groups = defaultdict(list)
for s in manual_signals:
    key = s["user_message"][:100].lower().strip()
    intent_groups[key].append(s)

# Rank by: group size (frequency) x avg token cost (effort)
intent_ranked = []
for key, signals in intent_groups.items():
    avg_tokens = sum(s["total_tokens"] for s in signals) / len(signals)
    avg_tools = sum(s["tool_count"] for s in signals) / len(signals)
    intent_ranked.append({
        "intent": signals[0]["user_message"][:200],
        "occurrences": len(signals),
        "avg_tokens": int(avg_tokens),
        "avg_tool_count": round(avg_tools, 1),
        "common_tools": list(set(
            t for s in signals for t in s["tools_used"]
        ))[:10],
        "sample_findings": signals[0]["findings"][:5],
        "priority_score": len(signals) * int(avg_tokens),
    })

intent_ranked.sort(key=lambda x: x["priority_score"], reverse=True)

# Add to the existing report
report["stress_signal_count"] = len(stress_signals)
report["manual_work_signals"] = len(manual_signals)
report["stress_candidates"] = intent_ranked[:10]

print(f"\n**Stress Signal Analysis:**")
print(f"  Total stress signals: {len(stress_signals)}")
print(f"  Manual work signals: {len(manual_signals)}")
print(f"  Unique intent groups: {len(intent_groups)}")
print(f"\n**Top Stress Candidates (intent + work):**")
for i, cand in enumerate(intent_ranked[:5], 1):
    print(f"  {i}. \"{cand['intent'][:80]}...\"")
    print(f"     occurrences={cand['occurrences']}, avg_tokens={cand['avg_tokens']}, "
          f"tools={cand['common_tools'][:5]}")

# Re-write the report with stress candidates included
out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
result = json.dumps(report)
```

[validate: report contains "stress_candidates" key]

## Notes

- The procedure mines `tool_call_requested`, `custom_tool_executed`, and `tool_exec_enter` events -- these are VaultBot's actual tool calls. It deliberately ignores `tool_call` events which are backend plumbing (ollama, vault_indexer, vault_graph, note_creator, vault_maintenance, llm_openai).
- The coverage heuristic (step 5) is intentionally simple: if an existing procedure name contains 50%+ of the tool names in a sequence, we consider it covered.
- N-grams are counted once per session (not per occurrence within a session) to avoid inflating counts from repetitive single-session patterns.
- The priority score is `frequency x sequence_length` -- longer sequences that repeat often are the strongest automation candidates.
- The suggested procedure name is a naive concatenation of tool names. The actual procedure should be designed with research, not just named after its tools.
