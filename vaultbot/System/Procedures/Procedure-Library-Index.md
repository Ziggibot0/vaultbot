---
type: index
status: active
baseline: true
created: 2026-08-01
summary: "Catalog of the procedure library, organized by when the big model should reach for each. The compounding design: ONE importable pattern-recognition engine (Pattern-Scan) recognizes patterns across LOTS of notes deterministically; every simple checking procedure just imports Pattern-Scan output (or is a thin filter over it) with a specific domain lens. The 30B big model never reasons over raw notes — it calls a procedure and reads the filtered result."
tags: [procedures, index, pattern-recognition, 30b, cartridge, compounding]
depends_on:
  - "[[How-to-Create-a-Procedure]]"
  - "[[Procedure-Expansion-Proposal]]"
  - "[[Local-30B-Big-Model-Plan]]"
---

# Procedure Library Index

This is the map of the procedure cartridge system. When the local 30B model
is the big cartridge, it should NOT sit and reason — it should call the
procedure that already encodes the "how". Procedures compound: the more
deterministic scanning/filtering is captured here, the less the big model
does over time.

## The Compounding Pattern (read this first)

```
                ┌──────────────────────────────────┐
                │  Pattern-Scan  (importable engine)  │
                │  walks EVERY note once, computes    │
                │  ~15 signals/note, writes JSON      │
                └──────────────┬─────────────────────┘
                               │ imports
                ┌──────────────┴─────────────────────┐
                │  Find-Orphans  │  Find-Broken  │  Find-Stubs  │ ...
                │  (filter: orphan) │ (filter: broken) │ (filter: thin) │
                └──────────────────────────────────────┘
```

A "simple checking procedure" is just **Pattern-Scan + one filter**. To
add a brand-new check, you don't rescan the vault — you filter the shared
table. That is how the big model does less and less: pattern recognition
is captured once, reused everywhere, at zero LLM cost.

## Pattern-Recognition Layer (the engine)

| Procedure | Cartridge | What it recognizes |
|---|---|---|
| [[Pattern-Scan]] | small | ALL per-note patterns across the whole vault — the single importable engine. Writes `pattern-scan-latest.json`. |

## Checking Layer (import Pattern-Scan, one filter each)

| Procedure | Filter (`is_*`/signal) | Question it answers |
|---|---|---|
| [[Find-Orphans]] | `is_orphan` | Which notes are disconnected islands? |
| [[Find-Broken-Links]] | `unresolved_out>0` | Which notes link to notes that don't exist? What should I create first? |
| [[Find-Stubs]] | `is_thin`/`is_stub` | Which notes are nearly empty / placeholders? |
| [[Find-Duplicates]] | `duplicates` map | Which notes exist more than once? |
| [[Find-Overdue-Tasks]] | `todo_count>0` | What's left to do across all notes? |
| [[Find-Stale-Notes]] | `is_stale` | Which load-bearing notes are >30d old? |
| [[Find-Unlinked-Mentions]] | raw-title match | Where can I add `[[links]]` with no new notes? |

## Meta / Aggregation Layer

| Procedure | Cartridge | What it does |
|---|---|---|
| [[Vault-Cleanup]] | small | Runs Pattern-Scan ONCE → single prioritized cleanup queue (orphans+broken+dupes+stubs+stale). |
| [[Vault-Health-Check]] | small | Pattern-Scan + graph analyzer → one health report. Session-start snapshot. |
| [[Note-Linker]] | small | After a write, suggests `[[links]]` for the most-recently-modified note. |
| [[Procedure-Eval]] | small | Scores every procedure's health from frontmatter counters + failure log. Called by Dream-Pass. |

## Troubleshooting & Self-Diagnosis Layer

Read-only procedures that let VaultBot (and the operator) see what
happened in any past session and what's wrong right now. All `small`
cartridge, all single code-step — no LLM cost, no new tools.

| Procedure | What it answers |
|---|---|
| [[Analyze-Session-Log]] | What happened in session X (by UUID, title, or "latest")? Full turns, tool calls, errors, tokens. |
| [[Find-Recent-Errors]] | What went wrong across the last N sessions? Sweeps for exceptions/console errors/failed tools/procedure-step failures. |
| [[Diagnose-System-Health]] | Is everything okay right now? Calls /health + /diagnose + /system/stats + /ollama/stats into one report. |
| [[Verify-Procedure-Discoverability]] | Is a procedure actually surfacing in RAG when a user says something relevant? Runs its when_to_use phrasings as test queries through the FusedRetriever. |
| [[Diagnose-Retrieval-Failure]] | Why isn't a note/procedure being found? Walks the pipeline stage-by-stage: FAISS → top-k → merged pool → rerank → boost, pinpoints where it falls out. |

Compose: run [[Diagnose-System-Health]] for triage → [[Find-Recent-Errors]]
to locate the failing session → [[Analyze-Session-Log]] to deep-dive it.
If a procedure isn't being used when it should be: [[Verify-Procedure-Discoverability]]
to test it → [[Diagnose-Retrieval-Failure]] to find the root cause.

## High-frequency core (existing, already good)

These were audited onto the small cartridge — run them freely:

- Session/status: [[VaultBot-Status]], [[Vault-List]], [[Vault-Gaps]]
- Quality: [[Vault-Lint]], [[Verify-Syntax]], [[Note-Accuracy-Check]], [[Cross-Check-Claims]], [[Refine-Concept-Card]]
- Cartridge helpers (all `small`): [[Note-Tags-From-Content]], [[Summarize-Conversation]], [[Condense-Note]], [[Extract-Entities]], [[Judge-Plan]], [[Regenerate-Self-Model]]
- Safety/maintenance: [[Preflight-Safety-Check]], [[Safe-Write]], [[JS-Safe-Write]], [[Vault-Delete]], [[Git-Rollback]], [[Backend-Restart]], [[Plugin-Reload]]
- Build/test: [[Code-Run]], [[Write-Python-Tool]], [[Torture-Test]], [[Fix-Indentation]], [[Run-Test-Suite]], [[Verify-Backend-Change]]
- Architecture: [[Prompt-Architecture-Audit]], [[Analyze-Function-Flow]], [[Smart-Code-Read]], [[Code-Structure-Check]]
- Research/ingest: [[Textbook-Ingest]], [[Textbook-Read-Page]], [[Ollama-Model-Search]], [[Ollama-Pull-Models]]
- Community: [[Review-Contributions]], [[Submit-Contribution]]
- Core loop behavior: [[Agentic-Loop-Turn-Protocol]] — mandatory phase-state machine for every chat turn; referenced by the system prompt.
- Self-improvement (big): [[Dream-Pass]] (now calls [[Procedure-Eval]]), [[Self-Reflect]], [[Capability-Audit]], [[Discover-Procedures]]

## Big-model-only procedures

Only these genuinely need the 30B model's reasoning — everything else is small:

| Procedure | Why big |
|---|---|
| [[Dream-Pass]] | Step 3 synthesizes cross-session semantic notes (multi-source reasoning). |
| [[Self-Reflect]] | Evaluates/prioritizes which new capabilities to build. |

## How to add a new capability (the cheap way)

1. Is it "find X pattern across many notes"? → add ONE filter reading
   `pattern-scan-latest.json`. Do NOT rescan. ~15 lines.
2. Is it a multi-step workflow? → new procedure calling `run_procedure(...)`
   to compose existing ones (see [[Vault-Cleanup]], [[Dream-Pass]] Step 6).
3. Follow [[How-to-Create-a-Procedure]]. Put `model_cartridge: small` unless
   the LLM step genuinely needs multi-step reasoning.
4. Give it `when_to_use` so the description-surface embedding can discover it.
5. After editing backend code, verify and deploy with [[Verify-Backend-Change]]
   (runs tests → restarts → checks health in one call).

Every procedure added this way is another thing the 30B model no longer
has to figure out — the procedure system compounding on itself.