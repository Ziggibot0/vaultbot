---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-09
updated: 2026-08-09
description: "Session-Effort-Analysis scans all chat logs to quantify token consumption, tool usage patterns, and time spent across sessions. It aggregates data per session, highlights the most token-heavy tools, and suggests new procedures that could consolidate repetitive sequences.

The report is written to `vaultbot/Memory/Build-Log/session-effort-analysis.json` and a concise human-readable summary is printed as the final output."
when_to_use: "Whenever you want an overview of where my tokens, time, and effort are going across all sessions.— Ideal before starting a new complex task or when debugging performance issues."
falsifiable_if: "The analysis misses a session file, miscounts tokens, or fails to identify the top 3 tools accurately."
applies_to:
  - self-knowledge
  - introspection
  - pattern-analysis
allowed_tools:
  - vault_list
  - vault_read_note
  - machine_spec
  - code_read
summary: |
  Session-Effort-Analysis aggregates chat logs to produce token usage statistics, tool call frequencies, and time spent per session.
  1. List all chat notes under `vaultbot/Memory/Chat`. 
  2. For each note, read metadata (tokens, timestamp) and extract ordered list of tool calls from the chat text. 
  3. Aggregate totals: total tokens per session, token per tool, time between first \u0026 last user message.
  4. Identify top token-heavy tools and top recurring tool chains.
  5. Suggest new composite procedures that could replace the most repetitive high-cost chains.
  6. Write a JSON report to `Memory/Build-Log/session-effort-analysis.json`.
  7. Print a concise summary for quick review.
tags:
  - procedure
  - self-knowledge
  - pattern-analysis
---

# Session-Effort-Analysis

## Purpose

Collects and aggregates token \u0026 tool usage data from all chat logs to surface where I spend the most resources.

## Inputs

No explicit arguments. The procedure scans the entire `vaultbot/Memory/Chat` directory.

## Output Contract

**File written:** `vaultbot/Memory/Build-Log/session-effort-analysis.json`

Human-readable summary is printed as the final output.

---

## Steps

1. ```python
import json, os, re, datetime
from pathlib import Path

# Resolve vault root (use injected vault_path from wrapper)
vault_root = Path(vault_path)
chat_dir = vault_root / "vaultbot" / "Memory" / "Chat"
output_dir = vault_root / "vaultbot" / "Memory" / "Build-Log"
output_dir.mkdir(parents=True, exist_ok=True)
out_file = output_dir / "session-effort-analysis.json"

# Step 1: List all chat notes
chat_files = list(chat_dir.rglob("*.md"))
if not chat_files:
    raise RuntimeError("No chat logs found in vaultbot/Memory/Chat.")

sessions = []
for chat_file in chat_files:
    # Extract session ID from path (assumes structure Chat/<session_id>/...)
    rel_parts = chat_file.relative_to(chat_dir).parts
    session_id = rel_parts[0] if len(rel_parts) > 1 else chat_file.stem

    content = chat_file.read_text(encoding="utf-8", errors="replace")
    # Extract metadata block (YAML frontmatter)
    meta_match = re.search(r"^---\s*\n(?P<meta>.*?)(?:\n---)", content, flags=re.S | re.M)
    tokens = 0
    timestamp = None
    if meta_match:
        try:
            import yaml
            meta = yaml.safe_load(meta_match.group("meta"))
            tokens = int(meta.get('tokens', 0))
            timestamp = meta.get('timestamp')
        except Exception:
            pass

    # Extract ordered list of tool calls (e.g., [tool:ToolName])
    tools = re.findall(r"\[tool:(.+?)\]", content)

    # Compute session duration from first and last user message timestamps if present
    times = re.findall(r"^.*timestamp:\s*(.*?)\s*$", content, flags=re.M)
    start_ts = times[0] if times else None
    end_ts = times[-1] if times else None
    duration_sec = 0
    if start_ts and end_ts:
        try:
            fmt = "%Y-%m-%d %H:%M:%S"
            start_dt = datetime.datetime.strptime(start_ts, fmt)
            end_dt = datetime.datetime.strptime(end_ts, fmt)
            duration_sec = int((end_dt - start_dt).total_seconds())
        except Exception:
            pass

    sessions.append({
        "session_id": session_id,
        "tokens": tokens,
        "tools": tools,
        "duration_sec": duration_sec,
        "file_path": str(chat_file.relative_to(vault_root)),
    })

# Aggregate totals
from collections import Counter
total_tokens = sum(s["tokens"] for s in sessions)
total_sessions = len(sessions)

# Tool frequency across all sessions
tool_counts = Counter()
for s in sessions:
    tool_counts.update(s["tools"])

# Token per tool (approximate by dividing session tokens among tools proportionally)
token_per_tool = Counter()
for s in sessions:
    if not s["tokens"] or not s["tools"]:
        continue
    share = s["tokens"] / len(s["tools"])  # equal split assumption
    for t in s["tools"]:
        token_per_tool[t] += share

# Identify top tool chains (bigrams)
bigram_counts = Counter()
for s in sessions:
    tools = s["tools"]
    for i in range(len(tools)-1):
        bigram = f"{tools[i]}->{tools[i+1]}"
        bigram_counts[bigram] += 1

# Suggest composite procedures: pick top chains that appear >2 times and consume >100 tokens on average
suggestions = []
for chain, freq in bigram_counts.most_common(10):
    if freq < 3:
        continue
    # Estimate average token consumption for this chain by sampling sessions containing it
    matching_sessions = [s for s in sessions if all(t in s["tools"] for t in chain.split('->'))]
    if matching_sessions:
        avg_tokens = sum(s["tokens"] for s in matching_sessions) / len(matching_sessions)
        if avg_tokens > 100:
            suggestions.append({"chain": chain, "frequency": freq, "avg_tokens": round(avg_tokens,2)})

report = {
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "total_sessions": total_sessions,
    "total_tokens": total_tokens,
    "average_tokens_per_session": round(total_tokens/total_sessions if total_sessions else 0,2),
    "tool_counts": dict(tool_counts),
    "token_per_tool": dict(token_per_tool),
    "top_bigram_chains": bigram_counts.most_common(10),
    "suggestions": suggestions,
}

# Write JSON report
out_file.write_text(json.dumps(report, indent=1), encoding="utf-8")

print(f"\n**Session-Effort-Analysis Report (generated {report['generated_at']})**\n"
      f"Total Sessions: {total_sessions}\n"
      f"Total Tokens Used: {total_tokens}\n"
      f"Average Tokens per Session: {round(total_tokens/total_sessions,2)}\n"
      f"Top Tools by Frequency: {sorted(tool_counts.items(), key=lambda x:x[1], reverse=True)[:5]}\n"
      f"Top Bigram Chains: {bigram_counts.most_common(3)}\n"
      f"Suggested Composite Procedures: {suggestions}\n"
      f"Full JSON report written to {out_file}")
```



Note references:
- [[Capability-Audit]]
- [[Diagnose-System-Health]]
- [[Vault-Health-Check]]
- [[Procedure-Eval]]

The procedure aggregates session data, then calculates tool frequency and bigram chains to identify high-cost usage patterns. By suggesting composite procedures for the most frequent token-heavy chains, it reduces repetitive overhead.



*The report generation logic uses `yaml.safe_load` for metadata extraction and `re` to parse tool calls. Token per tool is approximated by evenly splitting session tokens among the tools invoked, which gives a quick heuristic of where the bulk of token cost lies. The suggestion engine only proposes new procedures when a chain appears at least three times **and** consumes over 100 tokens on average—this balances specificity with meaningful impact.*



**Reasoning:** The heavy-cost analysis is derived from token totals per session and tool frequency. By aggregating bigram chains we can identify *repeated* sequences that consume a lot of tokens. Suggesting a composite procedure for such a chain reduces the number of individual tool calls, thereby cutting overhead. This aligns with our goal to let me operate without external prompts about which procedures to use.

