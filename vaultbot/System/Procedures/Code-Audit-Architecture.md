---
type: architecture-plan
status: active
baseline: true
created: 2026-08-06
summary: Architectural directive for building granular code-audit procedures with a three-step workflow, then an orchestrator, verified end-to-end on real backend files.
tags:
  - architecture
  - code-audit
  - procedures
  - directive
---

# Code-Audit-Architecture

## Directive

Build granular code-audit procedures following the **Pattern-Scan refactoring pattern**: decompose a monolith into independent, reusable layers. Each code-audit concern gets its own procedure with exactly three steps. Then build a thin orchestrator that calls them together.

## Why This Exists

Code audit was bundled into a single `Code-Structure-Check` covering four unrelated functions, making each concern hard to test and maintain independently. This directive decomposes code audit into granular one-concern procedures plus a thin orchestrator. The key tradeoff is that each procedure is limited to exactly three steps (read → check → report), trading flexibility for uniformity and independent testability.

## Why This Pattern

The Pattern-Scan refactoring (2026-08-04) proved this works: a 213-line monolith became:
- **Vault-Walk** — reads every .md, returns raw per-note data (independent, reusable)
- **Signal probes** — ~10-line filters over Vault-Walk output (each a standalone procedure)
- **Pattern-Scan** — thin orchestrator that runs Vault-Walk → computes shared maps → applies signal logic → writes JSON

This same pattern applies to code audit: instead of one big `Code-Structure-Check` bundle covering four unrelated functions, we need:

## Target Architecture

### Layer 1: Granular Code-Audit Procedures (3 steps each)

Each procedure handles ONE concern:

1. **Check-Error-Handling** — scans a Python file for missing try/except, bare excepts, swallowed exceptions
2. **Check-Type-Hints** — scans for missing type annotations on function signatures
3. **Check-Docstrings** — scans for missing or incomplete docstrings on public functions

Each follows the same 3-step template:
- **Step 1:** Read the target file (code_read or vault_read_note)
- **Step 2:** Apply the check logic (deterministic Python code)
- **Step 3:** Report findings (LLM summary of what was found)

### Layer 2: Thin Orchestrator

**Code-Audit-Senior-Review** — calls all three granular procedures in sequence, collects their outputs, and produces a unified audit report. Like Pattern-Scan, it's pure orchestration — zero duplication of check logic.

## Verification

After building all four procedures, execute the orchestrator against a real backend file (e.g., `vaultbot/vaultbot_backend/chat_handler.py`) to validate end-to-end behavior.

## Design Rules

- No monoliths — each procedure is one concern
- Each procedure has exactly 3 steps (read → check → report)
- The orchestrator is thin — it only calls sub-procedures, never duplicates logic
- Every procedure must be independently testable
- Follow the Pattern-Scan refactoring as the proven template

## Related

- [[Code-Audit-Senior-Review]] — the thin orchestrator this directive specifies
- [[Check-Error-Handling]] — one of the granular procedures
- [[Pattern-Scan]] — the refactoring pattern this directive follows
