---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-07-27
last_reviewed: 2026-08-04
description: "Biomimetic dream pass — thin orchestrator that runs 16 modular sub-procedures in sequence: Scan → Analyze → Dangle-Fix → Link → Chat-Consolidation → Session-Effort-Analysis → Behavioral-Pattern-Mine → Pattern-To-Procedure → Consolidate → Curate-Research → Prune → TODO-Track → Validate → Gap-Fill → Evaluate → When-To-Use-Update. Each sub-procedure can also be run independently. Uses small cartridge — all reasoning lives in sub-procedures (Dream-Consolidate and Dream-Pattern-To-Procedure carry their own big cartridges)."
when_to_use: "When asked to run a dream pass, when consolidating memories, when doing vault maintenance, when the vault needs offline processing, when fixing broken wikilinks, when cleaning up the vault, when consolidating chat logs, when mining patterns from conversations, when pruning junk notes, when validating vault health, when scanning for knowledge gaps, when creating procedures from patterns, or when the vault feels cluttered or disconnected"
falsifiable_if: it fails to improve graph connectivity, produces duplicate semantic notes, or crashes on any step
applies_to:
  - vault
  - memory
  - consolidation
  - dreaming
  - maintenance
allowed_tools:
  - run_procedure
  - vault_graph_analyzer
  - vault_list
  - vault_append
  - vault_delete
  - vault_lint
  - vault_search
  - code_read
  - llm_generate
provides:
  - Dream-Scan
  - Dream-Analyze
  - Dream-Dangle-Fix
  - Dream-Link
  - Chat-Consolidation
  - Session-Effort-Analysis
  - Behavioral-Pattern-Mine
  - Dream-Pattern-To-Procedure
  - Dream-Consolidate
  - Dream-Curate-Research
  - Dream-Prune
  - Dream-TODO-Track
  - Dream-Validate
  - Dream-Gap-Fill
  - Dream-Evaluate
  - Dream-When-To-Use-Update
success_count: 108
failure_count: 1
success_rate: 0.99
summary: "1. A thin orchestrator that calls 7 modular sub-procedures to process journals and chat logs by consolidating them in a step-by-step sequence, with reasoning logic stored within the [[Dream-Consolidat"
tags:
  - procedure
  - procedures
---

# Dream Pass (Orchestrator)

A biomimetic offline processing cycle inspired by how the brain consolidates memories during sleep. This is a **thin orchestrator** that calls 7 modular sub-procedures in sequence. Each sub-procedure is a standalone Lego brick that can be run independently or recombined.

## Cartridge Note

This orchestrator uses the **small** model cartridge. It contains zero `[llm:]` steps — every step is a `run_procedure()` call. The heavy reasoning lives in [[Dream-Consolidate]], which carries its own **big** cartridge and is invoked as a sub-procedure in Step 3.

## Architecture

```
Dream-Pass (orchestrator, small cartridge)
├── Dream-Scan            — Scan journals, extract themes
├── Dream-Analyze         — Graph health check (islands, orphans, dangling links)
├── Dream-Dangle-Fix      — Repair broken wikilinks, flag genuine gaps
├── Dream-Link            — Connect orphaned notes via semantic search
├── Chat-Consolidation    — Classify chat logs into pattern highways
├── Session-Effort-Analysis — Quantify token/tool usage across sessions
├── Behavioral-Pattern-Mine — Mine recurring tool sequences for automation
├── Dream-Pattern-To-Procedure — Auto-create procedures from mined patterns
├── Dream-Consolidate     — Write semantic notes from patterns (big cartridge, self-contained)
├── Dream-Curate-Research — Upgrade raw research notes, flag thin/junk
├── Dream-Prune           — Remove junk files
├── Dream-TODO-Track      — Scan for TODO markers, produce action report
├── Dream-Validate        — Verify graph is healthier (before/after)
├── Dream-Gap-Fill        — Create stub notes for genuine knowledge gaps
└── Dream-Evaluate        — Score the procedure library
└── Dream-When-To-Use-Update — Enrich thin procedure trigger language
```

## Cluster A: Error Handling, Telemetry, Conditional Dispatch, Prior Results

This orchestrator implements four fixes from [[Dream-Pass-Audit]]:

1. **Error handling between steps** — critical steps (Scan, Analyze, Link, Consolidate, Validate) raise `RuntimeError` on failure, halting the pass. Optional steps (Prune, Evaluate) log failures without halting.
2. **Telemetry** — every step records pass/fail/skipped status in a `telemetry` dict. The final result is the telemetry summary.
3. **Conditional dispatch** — Link is skipped if the graph is already healthy; Consolidate is skipped if no themes were found; Validate is skipped if the graph wasn't modified.
4. **Prior results** — each step's `final_output` is stored in `prior_results` and passed forward, replacing the old pattern of overwriting a single `result` variable.

## Steps

### Step 0: Scan — Extract Journal Themes

Calls [[Dream-Scan]] to scan recent journal entries (date-only filenames) for new content and extract themes. Saves themes to `prior_results` for downstream sub-procedures.

0. ```python
import json

# === Cluster A Initialization ===
telemetry = {"pass": 0, "fail": 0, "skipped": 0, "steps": {}}
# prior_results is provided by the runtime — do NOT reinitialize it

def _graph_is_healthy():
    try:
        d = json.loads(prior_results.get("analyze", "{}"))
        return d.get("connectivity_ratio", 0.0) > 0.7 and d.get("islands", 999) <= 3
    except (json.JSONDecodeError, TypeError):
        return False

def _has_themes():
    try:
        d = json.loads(prior_results.get("scan", "{}"))
        return len(d.get("themes", [])) > 0
    except (json.JSONDecodeError, TypeError):
        return False

def _graph_was_modified():
    return not (telemetry["steps"].get("link") == "skipped"
                and telemetry["steps"].get("consolidate") == "skipped")

# === Step 0: Scan (critical) ===
scan_result = run_procedure("Dream-Scan")
prior_results["scan"] = scan_result.get("final_output", "{}")
if scan_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["scan"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["scan"] = "fail"
    raise RuntimeError(f"Dream-Scan failed at step '{scan_result.get('failed_step', 'unknown')}'")

# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)
```

### Step 1: Analyze — Graph Health Check

Calls [[Dream-Analyze]] to run the vault graph analyzer and measure islands, isolated nodes, and connectivity ratio.

1. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

analyze_result = run_procedure("Dream-Analyze")
prior_results["analyze"] = analyze_result.get("final_output", "{}")
if analyze_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["analyze"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["analyze"] = "fail"
    raise RuntimeError(f"Dream-Analyze failed at step '{analyze_result.get('failed_step', 'unknown')}'")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 1.5: Dangle-Fix — Repair Broken Wikilinks

Calls [[Dream-Dangle-Fix]] to fuzzy-match dangling wikilinks surfaced by Dream-Analyze to existing vault notes and repair the broken references. Genuine gaps (no match) are flagged for Dream-Gap-Fill later. **Optional** — failures are logged but do not halt the pass.

1.5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

dangle_result = run_procedure("Dream-Dangle-Fix")
prior_results["dangle_fix"] = dangle_result.get("final_output", "{}")
if dangle_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["dangle_fix"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["dangle_fix"] = "fail"
    # Optional step — log but don't halt
    prior_results["dangle_fix_error"] = dangle_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 2: Link — Connect Orphaned Notes

Calls [[Dream-Link]] to find semantically related notes for each isolated node and add wikilinks to connect them into the graph. **Conditional dispatch** — skipped if the graph is already healthy (connectivity ratio > 0.7 and islands ≤ 3).

2. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

def _graph_is_healthy():
    try:
        d = json.loads(prior_results.get("analyze", "{}"))
        return d.get("connectivity_ratio", 0.0) > 0.7 and d.get("islands", 999) <= 3
    except (json.JSONDecodeError, TypeError):
        return False

def _has_themes():
    try:
        d = json.loads(prior_results.get("scan", "{}"))
        return len(d.get("themes", [])) > 0
    except (json.JSONDecodeError, TypeError):
        return False

def _graph_was_modified():
    return not (telemetry["steps"].get("link") == "skipped"
                and telemetry["steps"].get("consolidate") == "skipped")

if _graph_is_healthy():
    telemetry["skipped"] += 1
    telemetry["steps"]["link"] = "skipped"
    prior_results["link"] = "{}"
else:
    link_result = run_procedure("Dream-Link")
    prior_results["link"] = link_result.get("final_output", "{}")
    if link_result.get("overall_passed", False):
        telemetry["pass"] += 1
        telemetry["steps"]["link"] = "pass"
    else:
        telemetry["fail"] += 1
        telemetry["steps"]["link"] = "fail"
        raise RuntimeError(f"Dream-Link failed at step '{link_result.get('failed_step', 'unknown')}'")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 2.5: Chat-Consolidation — Classify Chat Logs into Pattern Highways

Calls [[Chat-Consolidation]] to scan unlinked chat logs, classify them into pattern highways (Build-Log, Design-Decisions, Testing-History), and route each to the appropriate specialized processor. **Optional** — failures are logged but do not halt the pass.

2.5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

chat_result = run_procedure("Chat-Consolidation")
prior_results["chat_consolidation"] = chat_result.get("final_output", "{}")
if chat_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["chat_consolidation"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["chat_consolidation"] = "fail"
    # Optional step — log but don't halt
    prior_results["chat_consolidation_error"] = chat_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 2.6: Session-Effort-Analysis — Quantify Token and Tool Usage

Calls [[Session-Effort-Analysis]] to scan all chat logs and quantify token consumption, tool usage patterns, and time spent across sessions. Aggregates data per session and highlights the most token-heavy tools. **Optional** — failures are logged but do not halt the pass.

2.6. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

effort_result = run_procedure("Session-Effort-Analysis")
prior_results["session_effort"] = effort_result.get("final_output", "{}")
if effort_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["session_effort"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["session_effort"] = "fail"
    # Optional step — log but don't halt
    prior_results["session_effort_error"] = effort_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 2.7: Behavioral-Pattern-Mine — Detect Automation Candidates

Calls [[Behavioral-Pattern-Mine]] to scan all chat logs for recurring tool-call sequences (n-grams of length 2-5) that appear across 3+ sessions and are NOT already covered by existing procedures. Surfaces these as automation candidates — manual patterns VaultBot repeats that should be consolidated into a procedure. **Optional** — failures are logged but do not halt the pass.

2.7. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

pattern_result = run_procedure("Behavioral-Pattern-Mine")
prior_results["behavioral_pattern"] = pattern_result.get("final_output", "{}")
if pattern_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["behavioral_pattern"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["behavioral_pattern"] = "fail"
    # Optional step — log but don't halt
    prior_results["behavioral_pattern_error"] = pattern_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 2.8: Pattern-To-Procedure — Design Real Procedures from Mined Patterns

Reads the Behavioral-Pattern-Mine report, picks the top qualifying candidate, and calls [[Dream-Pattern-To-Procedure]] to design a real procedure (with proper args and context) and pass it through [[Procedure-Creator]]'s validation pipeline (13 static checks + dry run sandbox). **Optional** — failures are logged but do not halt the pass. **Conditional dispatch** — skipped if Behavioral-Pattern-Mine found no uncovered patterns, or if all candidates are all-generic.

2.8. ```python
import json
from pathlib import Path

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

# Check if pattern mine found anything worth converting
try:
    pattern_data = json.loads(prior_results.get("behavioral_pattern", "{}"))
    uncovered = pattern_data.get("uncovered_patterns", 0)
except (json.JSONDecodeError, TypeError):
    uncovered = 0

if uncovered == 0:
    telemetry["skipped"] += 1
    telemetry["steps"]["pattern_to_procedure"] = "skipped"
    prior_results["pattern_to_procedure"] = "{}"
else:
    vault_root = Path(vault_path)
    
    # Step A: Call Dream-Pattern-To-Procedure (writes draft to _procedure_draft.md)
    ptp_result = run_procedure("Dream-Pattern-To-Procedure")
    
    # Check if a draft was written
    draft_path = vault_root / "_procedure_draft.md"
    if not draft_path.exists():
        telemetry["skipped"] += 1
        telemetry["steps"]["pattern_to_procedure"] = "skipped"
        prior_results["pattern_to_procedure"] = json.dumps({"reason": "no qualifying candidates"})
    else:
        # Step B: Call Procedure-Creator to validate (13 static checks + dry run) and publish
        try:
            creator_result = run_procedure("Procedure-Creator")
            if creator_result.get("overall_passed", False):
                telemetry["pass"] += 1
                telemetry["steps"]["pattern_to_procedure"] = "pass"
                prior_results["pattern_to_procedure"] = json.dumps({"status": "published"})
            else:
                telemetry["fail"] += 1
                telemetry["steps"]["pattern_to_procedure"] = "fail"
                prior_results["pattern_to_procedure"] = json.dumps({
                    "error": "Procedure-Creator validation failed",
                    "failed_step": creator_result.get("failed_step", "unknown"),
                })
        except Exception as e:
            telemetry["fail"] += 1
            telemetry["steps"]["pattern_to_procedure"] = "fail"
            prior_results["pattern_to_procedure"] = json.dumps({"error": str(e)})

# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 3: Consolidate — Write Semantic Notes from Patterns

Calls [[Dream-Consolidate]] to synthesize semantic knowledge notes from journal themes, graph gaps, and quality module patterns. Dream-Consolidate uses the **big** model cartridge — it carries its own cartridge independently, so this orchestrator can stay small. **Conditional dispatch** — skipped if no themes were found in Step 0.

3. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

def _graph_is_healthy():
    try:
        d = json.loads(prior_results.get("analyze", "{}"))
        return d.get("connectivity_ratio", 0.0) > 0.7 and d.get("islands", 999) <= 3
    except (json.JSONDecodeError, TypeError):
        return False

def _has_themes():
    try:
        d = json.loads(prior_results.get("scan", "{}"))
        return len(d.get("themes", [])) > 0
    except (json.JSONDecodeError, TypeError):
        return False

def _graph_was_modified():
    return not (telemetry["steps"].get("link") == "skipped"
                and telemetry["steps"].get("consolidate") == "skipped")

if not _has_themes():
    telemetry["skipped"] += 1
    telemetry["steps"]["consolidate"] = "skipped"
    prior_results["consolidate"] = "{}"
else:
    consolidate_result = run_procedure("Dream-Consolidate")
    prior_results["consolidate"] = consolidate_result.get("final_output", "{}")
    if consolidate_result.get("overall_passed", False):
        telemetry["pass"] += 1
        telemetry["steps"]["consolidate"] = "pass"
    else:
        telemetry["fail"] += 1
        telemetry["steps"]["consolidate"] = "fail"
        raise RuntimeError(f"Dream-Consolidate failed at step '{consolidate_result.get('failed_step', 'unknown')}'")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 3.5: Curate-Research — Upgrade Raw Research Notes

Calls [[Dream-Curate-Research]] to evaluate raw research notes on quality signals (wikilinks, chars, sources), upgrade curatable ones from `raw` to `active`, and flag thin/junk notes for review. **Optional** — failures are logged but do not halt the pass.

3.5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

curate_result = run_procedure("Dream-Curate-Research")
prior_results["curate_research"] = curate_result.get("final_output", "{}")
if curate_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["curate_research"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["curate_research"] = "fail"
    # Optional step — log but don't halt
    prior_results["curate_research_error"] = curate_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 4: Prune — Remove Junk and Stale Content

Calls [[Dream-Prune]] to scan for and remove pytest cache files, duplicate/backup files, corrupted filenames, and trash remnants. **Optional** — failures are logged but do not halt the pass.

4. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

prune_result = run_procedure("Dream-Prune")
prior_results["prune"] = prune_result.get("final_output", "{}")
if prune_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["prune"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["prune"] = "fail"
    # Optional step — log but don't halt
    prior_results["prune_error"] = prune_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 4.5: TODO-Track — Scan for Unfinished Work

Calls [[Dream-TODO-Track]] to scan all vault notes for TODO/FIXME/HACK/XXX/NOTE markers, group them by note, and produce a prioritized action report. **Optional** — failures are logged but do not halt the pass.

4.5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

todo_result = run_procedure("Dream-TODO-Track")
prior_results["todo_track"] = todo_result.get("final_output", "{}")
if todo_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["todo_track"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["todo_track"] = "fail"
    # Optional step — log but don't halt
    prior_results["todo_track_error"] = todo_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 5: Validate — Verify the Graph is Healthier

Calls [[Dream-Validate]] to run the graph analyzer again and compare before/after metrics. The graph should have fewer islands and higher connectivity. **Conditional dispatch** — skipped if neither Link nor Consolidate ran (graph wasn't modified).

5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

def _graph_is_healthy():
    try:
        d = json.loads(prior_results.get("analyze", "{}"))
        return d.get("connectivity_ratio", 0.0) > 0.7 and d.get("islands", 999) <= 3
    except (json.JSONDecodeError, TypeError):
        return False

def _has_themes():
    try:
        d = json.loads(prior_results.get("scan", "{}"))
        return len(d.get("themes", [])) > 0
    except (json.JSONDecodeError, TypeError):
        return False

def _graph_was_modified():
    return not (telemetry["steps"].get("link") == "skipped"
                and telemetry["steps"].get("consolidate") == "skipped")

if not _graph_was_modified():
    telemetry["skipped"] += 1
    telemetry["steps"]["validate"] = "skipped"
    prior_results["validate"] = "{}"
else:
    validate_result = run_procedure("Dream-Validate")
    prior_results["validate"] = validate_result.get("final_output", "{}")
    if validate_result.get("overall_passed", False):
        telemetry["pass"] += 1
        telemetry["steps"]["validate"] = "pass"
    else:
        telemetry["fail"] += 1
        telemetry["steps"]["validate"] = "fail"
        raise RuntimeError(f"Dream-Validate failed at step '{validate_result.get('failed_step', 'unknown')}'")

# Extract islands_before and islands_after for the validate gate
try:
    before_data = json.loads(prior_results.get("analyze", "{}"))
    after_data = json.loads(prior_results.get("validate", "{}"))
    islands_before = before_data.get("islands", 999)
    islands_after = after_data.get("islands", 999)
except (json.JSONDecodeError, TypeError):
    islands_before = 999
    islands_after = 999
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

[validate: islands_after <= islands_before]

### Step 5.5: Gap-Fill — Create Missing Notes for Knowledge Gaps

Calls [[Dream-Gap-Fill]] to create stub notes for dangling wikilinks that Dream-Dangle-Fix couldn't match to any existing vault note. These are genuine knowledge gaps — notes that are referenced but were never written. **Optional** — failures are logged but do not halt the pass.

5.5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

gap_result = run_procedure("Dream-Gap-Fill")
prior_results["gap_fill"] = gap_result.get("final_output", "{}")
if gap_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["gap_fill"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["gap_fill"] = "fail"
    # Optional step — log but don't halt
    prior_results["gap_fill_error"] = gap_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 6: Evaluate — Score the Procedure Library

Calls [[Dream-Evaluate]] to classify every procedure as healthy/degraded/broken and surface which need review, cartridge demotion, or retirement. **Optional** — failures are logged but do not halt the pass.

6. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

eval_result = run_procedure("Dream-Evaluate")
prior_results["evaluate"] = eval_result.get("final_output", "{}")
if eval_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["evaluate"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["evaluate"] = "fail"
    # Optional step — log but don't halt
    prior_results["evaluate_error"] = eval_result.get("failed_step", "unknown")

# Final telemetry summary
prior_results["telemetry"] = json.dumps(telemetry)
result = json.dumps(telemetry)
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

### Step 6.5: When-To-Use-Update — Enrich Thin Procedure Trigger Language

Calls [[Dream-When-To-Use-Update]] to scan all procedure notes for missing or thin `when_to_use` frontmatter fields, generate better trigger language via LLM, and update them in place. This is the self-improving retrieval feedback loop — procedures with poor `when_to_use` fields don't surface in RAG, so they never get used, so they never get improved. This step breaks that cycle. **Optional** — failures are logged but do not halt the pass.

6.5. ```python
import json

# Load persisted telemetry from prior_results (survives subprocess boundaries)
telemetry = json.loads(prior_results.get("telemetry", '{"pass": 0, "fail": 0, "skipped": 0, "steps": {}}'))

wtu_result = run_procedure("Dream-When-To-Use-Update")
prior_results["when_to_use_update"] = wtu_result.get("final_output", "{}")
if wtu_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["when_to_use_update"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["when_to_use_update"] = "fail"
    # Optional step — log but don't halt
    prior_results["when_to_use_update_error"] = wtu_result.get("failed_step", "unknown")
# Persist telemetry for next subprocess
prior_results["telemetry"] = json.dumps(telemetry)

```

## Running Sub-Procedures Individually

Each sub-procedure can be run standalone via `execute_procedure("Dream-Scan")` etc. This is useful when you only need one phase:

- **Just cleaned up?** Run `Dream-Prune` then `Dream-Validate`
- **New journals to process?** Run `Dream-Scan` then `Dream-Consolidate`
- **Graph feels fragmented?** Run `Dream-Analyze` then `Dream-Link`
- **Procedure library stale?** Run `Dream-Evaluate`

## History

This procedure was originally a monolithic 560-line file with all logic inline. On 2026-08-02 it was modularized into 7 sub-procedures for maintainability and reusability. The original logic is preserved in each sub-procedure — only the structure changed.

On 2026-08-03 the model cartridge was demoted from big to small — the orchestrator contains zero LLM steps, so the big cartridge was wasted. Dream-Consolidate (the only sub-procedure that needs big) carries its own cartridge independently.

On 2026-08-04 Cluster A fixes from [[Dream-Pass-Audit]] were implemented: error handling between steps (RuntimeError on critical step failures), telemetry tracking (pass/fail/skipped counters), conditional dispatch (skip Link if graph healthy, skip Consolidate if no themes, skip Validate if graph unmodified), and prior_results storage (each step's output persisted in a dict instead of overwriting a single `result` variable). Also fixed a latent bug where `islands_before`/`islands_after` were never set for the validate gate.

On 2026-08-09 two new sub-procedures were wired in: [[Session-Effort-Analysis]] (step 2.6) quantifies token and tool usage across all chat sessions, and [[Behavioral-Pattern-Mine]] (step 2.7) mines recurring tool-call sequences that aren't yet automated as procedures. Both run after Chat-Consolidation and before Consolidate, and both are optional (failures are logged but do not halt the pass).


On 2026-08-14 [[Dream-When-To-Use-Update]] was wired in as Step 6.5 (after Evaluate). It scans all procedure notes for missing or thin `when_to_use` frontmatter fields, generates better trigger language via LLM, and patches them in place. This closes the self-improving retrieval feedback loop: procedures with poor `when_to_use` fields don't surface in RAG, so they never get used, so they never get improved. This step breaks that cycle.

## Integration with Session-Effort-Analysis and Behavioral-Pattern-Mine

Session-Effort-Analysis is now a built-in step (2.6) in the Dream-Pass pipeline. It runs automatically after Chat-Consolidation. Behavioral-Pattern-Mine (step 2.7) complements it by detecting recurring tool sequences that should be automated as procedures.


