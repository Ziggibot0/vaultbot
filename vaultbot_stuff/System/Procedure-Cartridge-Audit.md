---
type: audit
status: verified
baseline: true
created: 2026-07-31
summary: "Audit of all 13 procedure notes in System/Procedures/ — classified each by LLM step complexity and set model_cartridge accordingly. 4 already small, 1 already big, 7 flipped to small, 1 flipped to big."
tags: [audit, procedures, model-cartridge, token-efficiency, small-llm]
depends_on:
  - "[[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]]"
---

# Procedure Cartridge Audit

## Context

Following the [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge|tiny LLM use case mapping]], this audit classifies every procedure in `System/Procedures/` by what its LLM step actually does, then sets `model_cartridge` to `small` or `big` accordingly. The goal: cut cloud token usage by routing bounded-output tasks (reporting, formatting, classification) to the local qwen3.5:0.8b model.

## Methodology

For each procedure, I read the full file and examined every `[llm: ...]` step. Classification criteria based on the research in [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]]:

- **small** = bounded output, formatting, reporting, classification, extraction — the LLM just converts structured data into prose
- **big** = multi-step reasoning, synthesis, evaluation, open-ended generation — the LLM needs to think
- **small (default)** = no LLM step at all (pure code procedures) — set to small as safe default

## Results

### Already Correct (5 procedures)

| Procedure | Cartridge | LLM Step | Why |
|---|---|---|---|
| VaultBot-Status | `small` ✅ | Format JSON → prose summary | Bounded reporting |
| Vault-Lint | `small` ✅ | Report lint results, list broken links | Bounded reporting |
| Vault-List | `small` ✅ | Summarize file list, report count | Bounded reporting |
| Vault-Gaps | `small` ✅ | Report gaps grouped by type, suggest priority | Bounded reporting + light classification |
| Dream-Pass | `big` ✅ | LLM synthesis of cross-session patterns (Step 3) | Multi-source reasoning |

### Flipped to Small (7 procedures)

| Procedure | Was | Now | LLM Step | Why Small |
|---|---|---|---|---|
| Capability-Audit | (none) | `small` | Report audit results, suggest gap-filling | Just summarizing tool list + noting what's missing |
| Preflight-Safety-Check | (none) | `small` | *No LLM step* — pure code | Default for code-only procedures |
| Torture-Test | (none) | `small` | *No LLM step* — pure code | Default for code-only procedures |
| Textbook-Ingest | (none) | `small` | *No LLM step* — pure code | Default for code-only procedures |
| Textbook-Read-Page | (none) | `small` | *No LLM step* — pure code | Default for code-only procedures |
| Review-Contributions | (none) | `small` | *No LLM step* — pure code | Default for code-only procedures |
| Submit-Contribution | (none) | `small` | *No LLM step* — pure code | Default for code-only procedures |

### Flipped to Big (1 procedure)

| Procedure | Was | Now | LLM Step | Why Big |
|---|---|---|---|---|
| Self-Reflect | (none) | `big` | "Identify which proposed abilities are most valuable and should be implemented first" | Needs evaluation, prioritization, reasoning about value |

## Final Tally

| Cartridge | Count | Cloud Tokens? |
|---|---|---|
| `small` | 11 | Zero — runs on local qwen3.5:0.8b |
| `big` | 2 | Cloud model — Dream-Pass + Self-Reflect |
| Total | 13 | |

**Before this audit:** 4 small, 1 big, 8 unset (defaulting to big = cloud model for everything).

**After this audit:** 11 small, 2 big. Only 2 procedures out of 13 actually need the cloud model. That's an 85% reduction in procedures that could burn cloud tokens.

## Key Insight

6 of 13 procedures have **no LLM step at all** — they're pure code execution. These were silently defaulting to the big cloud model if the runtime fell back to it. Setting them to `small` is a no-cost safety net: even if someone adds an LLM step later, it'll default to the cheap local model.

The only 2 procedures that genuinely need the cloud model are:
1. **Dream-Pass** — synthesizes cross-session patterns from chat logs (requires multi-source reasoning)
2. **Self-Reflect** — evaluates which new capabilities are most valuable (requires reasoning about priorities)

Everything else is just "run code, format the result, report to user" — exactly what sub-1B models excel at per the research.