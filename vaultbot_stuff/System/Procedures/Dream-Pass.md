---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-27
last_reviewed: 2026-08-03
description: "Biomimetic dream pass — thin orchestrator that runs 7 modular sub-procedures in sequence: Scan → Analyze → Link → Consolidate → Prune → Validate → Evaluate. Each sub-procedure can also be run independently. Uses small cartridge — all reasoning lives in sub-procedures (Dream-Consolidate carries its own big cartridge)."
when_to_use: "when asked to run a dream pass, when consolidating memories, when doing vault maintenance, or when the vault needs offline processing"
falsifiable_if: "it fails to improve graph connectivity, produces duplicate semantic notes, or crashes on any step"
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
---

# Dream Pass (Orchestrator)

A biomimetic offline processing cycle inspired by how the brain consolidates memories during sleep. This is now a **thin orchestrator** that calls 7 modular sub-procedures in sequence. Each sub-procedure is a standalone Lego brick that can be run independently or recombined.

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

## Step 0: Scan — Extract Journal Themes

Calls [[Dream-Scan]] to scan recent journal entries (date-only filenames) for new content and extract themes. Saves themes to a temp file for downstream sub-procedures.

0. ```python
import json

scan_result = run_procedure("Dream-Scan")
result = scan_result.get("final_output", "{}")
```

### Step 1: Analyze — Graph Health Check

Calls [[Dream-Analyze]] to run the vault graph analyzer and measure islands, isolated nodes, and connectivity ratio.

1. ```python
import json

analyze_result = run_procedure("Dream-Analyze")
result = analyze_result.get("final_output", "{}")
```

### Step 2: Link — Connect Orphaned Notes

Calls [[Dream-Link]] to find semantically related notes for each isolated node and add wikilinks to connect them into the graph.

2. ```python
import json

link_result = run_procedure("Dream-Link")
result = link_result.get("final_output", "{}")
```

### Step 3: Consolidate — Write Semantic Notes from Patterns

Calls [[Dream-Consolidate]] to synthesize semantic knowledge notes from journal themes, graph gaps, and quality module patterns. Dream-Consolidate uses the **big** model cartridge — it carries its own cartridge independently, so this orchestrator can stay small.

3. ```python
import json

consolidate_result = run_procedure("Dream-Consolidate")
result = consolidate_result.get("final_output", "{}")
```

### Step 4: Prune — Remove Junk and Stale Content

Calls [[Dream-Prune]] to scan for and remove pytest cache files, duplicate/backup files, corrupted filenames, and trash remnants.

4. ```python
import json

prune_result = run_procedure("Dream-Prune")
result = prune_result.get("final_output", "{}")
```

### Step 5: Validate — Verify the Graph is Healthier

Calls [[Dream-Validate]] to run the graph analyzer again and compare before/after metrics. The graph should have fewer islands and higher connectivity.

5. ```python
import json

validate_result = run_procedure("Dream-Validate")
result = validate_result.get("final_output", "{}")
```

[validate: islands_after <= islands_before]

### Step 6: Evaluate — Score the Procedure Library

Calls [[Dream-Evaluate]] to classify every procedure as healthy/degraded/broken and surface which need review, cartridge demotion, or retirement.

6. ```python
import json

eval_result = run_procedure("Dream-Evaluate")
result = eval_result.get("final_output", "{}")
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