---
type: procedure
status: experimental
baseline: true
created: 2026-08-09
description: Orchestrator that runs a suite of library-maintenance probes and produces a unified procedure-library health report. Aggregates redundancy, underuse, staleness, failure patterns, coverage gaps, and drift into a single JSON report.
when_to_use: When you need a comprehensive view of the procedure library's health — after a batch of new procedures, before a promotion cycle, or when the autonomous researcher reports gaps.
falsifiable_if: Any sub-procedure returns empty results when data exists, or the aggregated report is missing a section.
applies_to:
  - procedure-library
  - maintenance
  - orchestration
  - health-check
allowed_tools:
  - run_procedure
  - vault_safe_write
  - vault_read_note
  - vault_list
provides:
  - Find-Redundant-Procedures
  - Find-Underused-Procedures
  - Find-Unverified-Procedures
  - Analyze-Failure-Log
  - Check-Procedure-Drift
  - Procedure-Coverage-Check
summary: SUMMARY
tags:
  - procedure
  - library-health
  - orchestration
  - maintenance
---

# Procedure-Library-Health

## Purpose

Produce a comprehensive health report for the entire procedure library by
running six maintenance probes and aggregating their results into a single
structured report.

## Why This Exists

Individual maintenance probes (redundancy, underuse, staleness, failure, drift, coverage) each answer one narrow question, but no single view aggregates them. This procedure closes that gap by orchestrating all six probes into one unified health report. The tradeoff is that it is a pure orchestrator — it adds no new analysis, only aggregation and a single output file.

## Inputs

- `vault_path` (string, default: ".") — path to the vault root

## Output

A JSON report written to `Memory/Build-Log/procedure-library-health.json`:

```json
{
  "timestamp": "ISO-8601",
  "redundant_procedures": [...],
  "underused_procedures": [...],
  "unverified_procedures": [...],
  "failure_patterns": [...],
  "drifted_procedures": [...],
  "coverage_gaps": [...],
  "summary": {
    "total_procedures": N,
    "redundant_count": N,
    "underused_count": N,
    "unverified_count": N,
    "failure_count": N,
    "drifted_count": N,
    "gap_count": N
  }
}
```

## Steps

### Step 1: Run Redundancy Probe
Call `run_procedure("Find-Redundant-Procedures")` to find near-duplicate
procedures. Store the result list.

### Step 2: Run Underuse Probe
Call `run_procedure("Find-Underused-Procedures")` to find procedures with
zero uses in the failure log. Store the result list.

### Step 3: Run Staleness Probe
Call `run_procedure("Find-Unverified-Procedures")` to find procedures stuck
at `experimental` status. Store the result list.

### Step 4: Run Failure Probe
Call `run_procedure("Analyze-Failure-Log")` to extract failure patterns
from the procedure failure log. Store the result list.

### Step 5: Run Drift Probe
Call `run_procedure("Check-Procedure-Drift")` to find procedures whose
embedding has drifted from their stated intent. Store the result list.

### Step 6: Run Coverage Probe
Call `run_procedure("Procedure-Coverage-Check")` to find task types that
have no corresponding procedure. Store the result list.

### Step 7: Aggregate and Write
Combine all results into the JSON structure above. Compute summary counts.
Write to `Memory/Build-Log/procedure-library-health.json` using
`vault_safe_write`. Return the summary as a human-readable message.

## Validation

- All six sub-procedure calls completed without error
- The output file exists and is valid JSON
- The summary counts are non-negative integers
- The timestamp is a valid ISO-8601 string

## Related

- [[Procedure-Eval]] — scores individual procedure health from counters
- [[Procedure-Coverage-Check]] — the coverage probe this procedure runs
- [[Find-Redundant-Procedures]] — the redundancy probe this procedure runs
