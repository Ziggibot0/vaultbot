---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-09
description: "Dream-Pattern-To-Procedure reads the Behavioral-Pattern-Mine report, picks the best candidate, reads session context, and deterministically generates a procedure with [llm:] steps and proper YAML frontmatter. The calling code (Dream-Pass step 2.8) writes the draft and calls Procedure-Creator for validation."
when_to_use: "During Dream-Pass, immediately after Behavioral-Pattern-Mine."
falsifiable_if: "The generated procedure fails Procedure-Creator validation, or no qualifying candidates exist."
applies_to:
  - automation
  - procedure-creation
  - dream-pass
  - feedback-loop
allowed_tools:
  - vault_read_note
  - code_read
summary: |
  Dream-Pattern-To-Procedure: deterministic procedure generation from pattern mine data.
  1. Read the behavioral-pattern-mine.json report and filter to best candidate.
  2. Read session context to understand the pattern.
  3. Deterministically generate a procedure with [llm:] steps and proper YAML.
  The calling code handles writing to _procedure_draft.md and calling Procedure-Creator.
tags:
  - procedure
  - automation
  - dream-pass
  - feedback-loop
---

# Dream-Pattern-To-Procedure

## Purpose

Closes the feedback loop between [[Behavioral-Pattern-Mine]] and the procedure library. This procedure:

1. Reads the pattern mine report and picks the best qualifying candidate
2. Reads a sample session log to understand *why* the pattern occurs
3. Deterministically generates a procedure with `[llm:]` steps and proper YAML frontmatter

The **calling code** (Dream-Pass step 2.8) writes the generated draft to `_procedure_draft.md` and calls [[Procedure-Creator]] to validate (13 static checks + dry run) and publish.

## Why This Exists

Mined tool-call patterns are raw sequences, not procedures — they need a name, YAML frontmatter, and `[llm:]` steps before they can be published. This procedure exists to deterministically generate a procedure from the pattern mine report, closing the loop between pattern detection and the procedure library. The key tradeoff is that it filters out all-generic tool sequences and low-priority candidates, so only genuinely novel workflows become procedures.

## Inputs

No explicit arguments. Reads `vaultbot/Memory/Build-Log/behavioral-pattern-mine.json`.

## Output Contract

The `final_output` is the generated procedure as markdown. The calling code handles the rest.

---

## Steps

### Step 1: Generate a procedure from the pattern mine report

1. ```python
import json
from pathlib import Path
from datetime import datetime, timezone

vault_root = Path(vault_path)
report_path = vault_root / "vaultbot" / "Memory" / "Build-Log" / "behavioral-pattern-mine.json"

if not report_path.exists():
    raise RuntimeError("No pattern mine report found. Run Behavioral-Pattern-Mine first.")

report = json.loads(report_path.read_text(encoding="utf-8"))
candidates = report.get("top_candidates", [])

# Generic tools that don't indicate a specific workflow
GENERIC_TOOLS = {"execute_procedure", "vault_read_note", "code_read", "code_run", 
                 "thought", "vault_search", "vault_list", "plan_task", "update_task"}

# Filter to the best candidate that isn't all-generic
best_candidate = None
for c in candidates:
    tools = [t.strip() for t in c["sequence"].split("->")]
    unique_tools = set(tools)
    if len(unique_tools) == 1:
        continue
    if c["priority_score"] < 20:
        continue
    if all(t in GENERIC_TOOLS for t in tools):
        continue
    best_candidate = c
    break

# Also check stress_candidates (intent+work signals from Behavioral-Pattern-Mine Step 2)
# These contain the user's actual intent + tools used + token cost, which gives
# the procedure context about WHY the pattern occurs, not just WHAT tools were called.
stress_candidates = report.get("stress_candidates", [])
stress_context = ""
if stress_candidates:
    # Find a stress candidate whose tools overlap with the best candidate
    for sc in stress_candidates:
        sc_tools = set(sc.get("common_tools", []))
        if sc_tools & set(tools if best_candidate else []):
            stress_context = (
                "\n\n## Intent Context (from stress signals)\n\n"
                "User intent: \"" + sc.get("intent", "")[:300] + "\"\n"
                "Occurrences: " + str(sc.get("occurrences", 0)) + "\n"
                "Avg tokens: " + str(sc.get("avg_tokens", 0)) + "\n"
                "Common tools: " + ", ".join(sc.get("common_tools", [])[:10]) + "\n"
                "Sample findings: " + "; ".join(sc.get("sample_findings", [])[:3])
            )
            break
    if not stress_context and stress_candidates:
        # Use the top stress candidate even if no tool overlap
        top_sc = stress_candidates[0]
        stress_context = (
            "\n\n## Intent Context (from stress signals)\n\n"
            "User intent: \"" + top_sc.get("intent", "")[:300] + "\"\n"
            "Occurrences: " + str(top_sc.get("occurrences", 0)) + "\n"
            "Avg tokens: " + str(top_sc.get("avg_tokens", 0)) + "\n"
            "Common tools: " + ", ".join(top_sc.get("common_tools", [])[:10]) + "\n"
            "Sample findings: " + "; ".join(top_sc.get("sample_findings", [])[:3])
        )

if best_candidate is None:
    # If we have stress candidates but no n-gram candidates, use the stress
    # candidate's tools as the basis for a new procedure.
    if stress_candidates:
        top_sc = stress_candidates[0]
        sc_tools = top_sc.get("common_tools", [])
        if sc_tools:
            best_candidate = {
                "sequence": " -> ".join(sc_tools[:5]),
                "length": len(sc_tools[:5]),
                "session_count": top_sc.get("occurrences", 0),
                "priority_score": top_sc.get("priority_score", 0),
                "suggested_procedure_name": "Stress-Heal-" + "-".join(t.replace("_", "-") for t in sc_tools[:3]),
            }
            print("No n-gram candidates, but using top stress candidate: " + best_candidate["sequence"])
        else:
            print("No qualifying candidates found (all are generic or low-priority).")
            result = json.dumps({"status": "skipped", "reason": "no qualifying candidates"})
    else:
        print("No qualifying candidates found (all are generic or low-priority).")
        result = json.dumps({"status": "skipped", "reason": "no qualifying candidates"})
else:
    # Read a sample session log to provide context
    sessions_dir = vault_root / "vaultbot" / "vaultbot_backend" / "sessions"
    session_files = sorted(sessions_dir.rglob("*.jsonl"), reverse=True)
    
    target_tools = set(t.strip() for t in best_candidate["sequence"].split("->"))
    session_context = ""
    for sf in session_files[:50]:
        tools_in_session = set()
        for line in sf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except:
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
                tools_in_session.add(tool)
        if target_tools.issubset(tools_in_session):
            session_context = " (observed in session " + sf.stem + ")"
            break
    
    tools = [t.strip() for t in best_candidate["sequence"].split("->")]
    seq = best_candidate["sequence"]
    sess_count = str(best_candidate["session_count"])
    pri_score = str(best_candidate["priority_score"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Generate a meaningful name based on what the pattern ACCOMPLISHES
    # Map common tool combinations to descriptive names
    name_map = {
        ("safe_replace", "safe_replace", "backend_restart"): "Apply-Fixes-and-Restart",
        ("safe_replace", "backend_restart"): "Apply-Fix-and-Restart",
        ("code_read", "safe_replace", "backend_restart"): "Audit-Fix-and-Restart",
        ("code_read", "safe_replace", "safe_replace", "backend_restart"): "Audit-Apply-Fixes-and-Restart",
        ("code_read", "code_read", "safe_replace", "safe_replace", "code_run"): "Multi-File-Audit-and-Fix",
        ("md_safe_replace", "md_safe_replace"): "Apply-Markdown-Fixes",
        ("vault_search", "vault_read_note", "vault_safe_write"): "Research-and-Write",
        ("vault_search", "code_read", "safe_replace"): "Search-Audit-Fix",
    }
    
    tool_tuple = tuple(tools)
    name = name_map.get(tool_tuple)
    if name is None:
        # Fallback: use first and last tool with a descriptor
        first = tools[0].replace("_", "-")
        last = tools[-1].replace("_", "-")
        name = first + "-to-" + last
    
    # Generate [llm:] steps with context-aware descriptions
    step_templates = {
        "safe_replace": "Apply the fix using safe_replace on the target file",
        "backend_restart": "Restart the backend with backend_restart to pick up changes",
        "code_read": "Read the target file with code_read to understand current state",
        "code_run": "Run a verification test with code_run to confirm the fix",
        "vault_safe_write": "Write results to a vault note with vault_safe_write",
        "vault_read_note": "Read the relevant vault note with vault_read_note",
        "vault_search": "Search the vault for related context with vault_search",
        "vault_lint": "Lint the modified note with vault_lint",
        "md_safe_replace": "Apply the markdown fix with md_safe_replace",
        "execute_procedure": "Run the sub-procedure with execute_procedure",
        "plan_task": "Create a task plan with plan_task",
        "update_task": "Update task status with update_task",
        "ask_user": "Ask the user for input with ask_user",
        "thought": "Think through the problem with thought",
    }
    
    steps = []
    for i, tool in enumerate(tools, 1):
        desc = step_templates.get(tool, "Call " + tool)
        short = desc.split(" with ")[0] if " with " in desc else desc
        steps.append("### Step " + str(i) + ": " + short + "\n\n[llm: " + desc + "]")
    steps_text = "\n\n".join(steps)
    
    # Build the full procedure
    tools_yaml = "\n".join("  - " + t for t in sorted(set(tools)))
    
    procedure = (
        "---\n"
        "type: procedure\n"
        "status: experimental\n"
        "baseline: false\n"
        "model_cartridge: small\n"
        "created: " + now + "\n"
        "description: \"Auto-generated from Behavioral-Pattern-Mine. Wraps the recurring pattern: " + seq + ". Observed in " + sess_count + " sessions" + session_context + " (priority: " + pri_score + ").\"\n"
        "when_to_use: \"When you need to " + seq.replace(' -> ', ', then ') + ".\"\n"
        "falsifiable_if: \"The pattern " + seq + " does not actually recur in practice, or the sequence is better done with individual tool calls.\"\n"
        "applies_to:\n"
        "  - automation\n"
        "  - auto-generated\n"
        "allowed_tools:\n"
        + tools_yaml + "\n"
        "summary: |\n"
        "  Auto-generated from Behavioral-Pattern-Mine. Wraps the sequence: " + seq + ".\n"
        "  Observed in " + sess_count + " sessions" + session_context + ".\n"
        "tags:\n"
        "  - procedure\n"
        "  - auto-generated\n"
        "  - experimental\n"
        "---\n"
        "\n"
        "# " + name + "\n"
        "\n"
        "## Purpose\n"
        "\n"
        "Auto-generated procedure for the recurring tool-call pattern observed across " + sess_count + " sessions" + session_context + ": `" + seq + "`.\n"
        "\n"
        "This procedure was created by [[Dream-Pattern-To-Procedure]] during a [[Dream-Pass]]. It starts as experimental -- the grading loop will promote or demote it based on actual usage.\n"
        "\n"
        "It is NON-baseline (personal): it was mined from this instance's own session logs, so it must not ship to other users. Promotion to `baseline: true` is a deliberate, reviewed act -- never an automatic side effect of a dream pass.\n"
        "\n"
        "## Steps\n"
        "\n"
        + steps_text + "\n"
        "\n"
        "[validate: at_least 0 result]\n"
    )
    
    print("Generated procedure: " + name + " (" + str(len(procedure)) + " chars)")
    
    # Write the draft directly to _procedure_draft.md
    draft_path = vault_root / "_procedure_draft.md"
    draft_path.write_text(procedure, encoding="utf-8")
    print("Draft written to _procedure_draft.md")
    
    result = procedure

```

[validate: contains "---" and contains "type: procedure" and contains "## Steps"]

## Related

- [[Behavioral-Pattern-Mine]] — produces the pattern report this consumes
- [[Procedure-Creator]] — validates and publishes the generated draft
- [[Dream-Pass]] — the orchestrator that calls this
