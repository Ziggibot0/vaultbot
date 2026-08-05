---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-27
last_reviewed: 2026-08-04
description: "Biomimetic dream pass — thin orchestrator that runs 7 modular sub-procedures in sequence: Scan → Analyze → Link → Consolidate → Prune → Validate → Evaluate. Each sub-procedure can also be run independently. Uses small cartridge — all reasoning lives in sub-procedures (Dream-Consolidate carries its own big cartridge)."
when_to_use: when asked to run a dream pass, when consolidating memories, when doing vault maintenance, or when the vault needs offline processing
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
success_count: 108
failure_count: 1
success_rate: 0.99
summary: Dream Pass (Orchestrator)
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
├── Dream-Scan        — Scan journals, extract themes
├── Dream-Analyze     — Graph health check (islands, orphans)
├── Dream-Link        — Connect orphaned notes via semantic search
├── Dream-Consolidate — Write semantic notes from patterns (big cartridge, self-contained)
├── Dream-Prune       — Remove junk files
├── Dream-Validate    — Verify graph is healthier (before/after)
└── Dream-Evaluate    — Score the procedure library
```

## Cluster A: Error Handling, Telemetry, Conditional Dispatch, Prior Results

This orchestrator implements four fixes from [[Dream-Pass-Audit]]:

1. **Error handling between steps** — critical steps (Scan, Analyze, Link, Consolidate, Validate) raise `RuntimeError` on failure, halting the pass. Optional steps (Prune, Evaluate) log failures without halting.
2. **Telemetry** — every step records pass/fail/skipped status in a `telemetry` dict. The final result is the telemetry summary.
3. **Conditional dispatch** — Link is skipped if the graph is already healthy; Consolidate is skipped if no themes were found; Validate is skipped if the graph wasn't modified.
4. **Prior results** — each step's `final_output` is stored in `prior_results` and passed forward, replacing the old pattern of overwriting a single `result` variable.

## Step 0: Scan — Extract Journal Themes

Calls [[Dream-Scan]] to scan recent journal entries (date-only filenames) for new content and extract themes. Saves themes to `prior_results` for downstream sub-procedures.

0. ```python
import json

# === Cluster A Initialization ===
prior_results = {}
telemetry = {"pass": 0, "fail": 0, "skipped": 0, "steps": {}}

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
```

### Step 1: Analyze — Graph Health Check

Calls [[Dream-Analyze]] to run the vault graph analyzer and measure islands, isolated nodes, and connectivity ratio.

1. ```python
import json

analyze_result = run_procedure("Dream-Analyze")
prior_results["analyze"] = analyze_result.get("final_output", "{}")
if analyze_result.get("overall_passed", False):
    telemetry["pass"] += 1
    telemetry["steps"]["analyze"] = "pass"
else:
    telemetry["fail"] += 1
    telemetry["steps"]["analyze"] = "fail"
    raise RuntimeError(f"Dream-Analyze failed at step '{analyze_result.get('failed_step', 'unknown')}'")
```

### Step 2: Link — Connect Orphaned Notes

Calls [[Dream-Link]] to find semantically related notes for each isolated node and add wikilinks to connect them into the graph. **Conditional dispatch** — skipped if the graph is already healthy (connectivity ratio > 0.7 and islands ≤ 3).

2. ```python
import json

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
```

### Step 3: Consolidate — Write Semantic Notes from Patterns

Calls [[Dream-Consolidate]] to synthesize semantic knowledge notes from journal themes, graph gaps, and quality module patterns. Dream-Consolidate uses the **big** model cartridge — it carries its own cartridge independently, so this orchestrator can stay small. **Conditional dispatch** — skipped if no themes were found in Step 0.

3. ```python
import json

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
```

### Step 4: Prune — Remove Junk and Stale Content

Calls [[Dream-Prune]] to scan for and remove pytest cache files, duplicate/backup files, corrupted filenames, and trash remnants. **Optional** — failures are logged but do not halt the pass.

4. ```python
import json

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
```

### Step 5: Validate — Verify the Graph is Healthier

Calls [[Dream-Validate]] to run the graph analyzer again and compare before/after metrics. The graph should have fewer islands and higher connectivity. **Conditional dispatch** — skipped if neither Link nor Consolidate ran (graph wasn't modified).

5. ```python
import json

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
```

[validate: islands_after <= islands_before]

### Step 6: Evaluate — Score the Procedure Library

Calls [[Dream-Evaluate]] to classify every procedure as healthy/degraded/broken and surface which need review, cartridge demotion, or retirement. **Optional** — failures are logged but do not halt the pass.

6. ```python
import json

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