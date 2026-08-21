---
type: research
status: active
baseline: true
created: 2026-08-03
tags:
  - small-models
  - architecture
  - routing
  - procedures
  - two-slot
summary: Design standard for making VaultBot drivable by weak models — two model slots (big + small), router-first dispatch, code-step-heavy deterministic procedures, cartridge pinning, and safety gating inside procedures. No mode-switching — just two slots.
research_backing:
  - "[[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]] — deterministic scaffolding with decision trees guides small models reliably"
  - "[[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]] — System 2 reflective loops improve outputs without retraining"
  - "[[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] — rubric design and calibration convert LLM-as-judge into reliable quality signals"
---

# Small-Model-Driving-Architecture

## Vision

> Shitty laptop + free OpenRouter model = working AI.

The intelligence lives in the vault's procedures, not in the model's weights. A small model is a **router**, not a reasoner. It classifies intent, dispatches to deterministic procedure chains, and reports results. The procedures do the hard thinking — they carry their own model cartridges, run code steps deterministically, and call each other in chains.

## Two-Slot Architecture (No Mode-Switching)

VaultBot has **two model slots** — a big model slot and a small model slot. That's it. There is no `SMALL_MODEL_MODE` flag, no conditional tool gating, no "if small model then hide tools." Both slots get the same tool list. The architecture works because:

1. **The tool list is already minimal.** The three-tier system (core/contextual/procedure) keeps the visible surface small regardless of which model is in which slot. A small model sees the same core tools as a big model — it just uses them differently (more procedure calls, less raw reasoning).

2. **Procedures carry their own cartridges.** When a procedure needs heavy reasoning, it declares its own `model_cartridge` in frontmatter and uses the big slot internally. The small model in the driver's seat just calls `execute_procedure` and reads the result — it never touches the heavy lifting.

3. **Safety is procedure-internal, not tool-gated.** Dangerous tools (`safe_write`, `code_run`, `tool_create`, `vault_delete`, `backend_restart`, `git_rollback`) are never advertised to ANY model — they exist only inside procedures with their own preflight checks. A small model can't destroy anything because it can't see the tools that could.

The old `SMALL_MODEL_MODE` concept (a flag that would hide Tier 2 tools when a small model was active) is **dead**. It was never implemented in code and is now explicitly rejected. The two-slot design is simpler: same tools, same system prompt, same rules. The model in the small slot just routes more and reasons less.

## Core Principles

### 1. Router-First

Every user request enters through [[Route-Task]], the master dispatcher. The model classifies the intent (a cheap classification task), and Route-Task returns a `procedure_chain` — an ordered list of procedure names. The model then calls each procedure in sequence. It never invents a workflow from raw tools.

```
User request → Route-Task (classify) → procedure_chain → execute each in order
```

### 2. Minimal Visible Toolset (For All Models)

All models see the same core tools. The three-tier system in `agent_tools.py` keeps the surface small:

| Tier | Tools | Visibility |
|------|-------|------------|
| **Tier 1: CORE** | `vault_read_note`, `code_read`, `plan_task`, `update_task`, `execute_procedure`, `vault_safe_write` (6) | Always sent |
| **Tier 2: CONTEXTUAL** | `research`, `code_edit`, `vault_maintenance`, `self_improvement`, `status` categories (~16) | Keyword-gated via `select_contextual_tools()` |
| **Tier 3: PROCEDURE_CANDIDATES** | `preflight_safety_check`, `torture_test`, etc. (10) | Never advertised — procedure-internal only |

A small model naturally gravitates toward `execute_procedure` because procedures are the path of least resistance — they're deterministic, graded, and self-healing. A big model might use more raw tools, but both see the same list.

### 3. Code-Step-Heavy Procedures

Procedures do their work in **deterministic code steps** (zero LLM cost). A procedure like [[Know-Thyself]] runs 9 code steps — each calling `run_procedure()` to probe a subsystem — and only uses LLM steps for synthesis. The model never touches the code; it just calls `execute_procedure('Know-Thyself')` and reads the result.

### 4. Cartridge Pinning

Each procedure declares its `model_cartridge` in frontmatter. When a procedure needs an LLM step, it uses its own cartridge — not the session model. This means a small model can route to a procedure that internally uses the big model slot for a specific reasoning step, then returns the result. The small model stays in the driver's seat but delegates heavy cognition to procedure cartridges.

### 5. Safety Gating Inside Procedures

Dangerous tools are **never advertised** to any model. They exist only inside procedures, which have their own safety checks (preflight, syntax validation, atomic writes, auto-rollback). No model — big or small — can destroy the vault or the backend through raw tool calls.

## The Bootstrap Contract

[[Small-Model-Bootstrap]] operationalizes this architecture at session start. It prints the operating contract:

1. **Procedures first, always.** Every request goes through Route-Task.
2. **Never improvise.** If no procedure fits, stop and say so.
3. **One step at a time.** After each tool result, either answer or call exactly one more tool.
4. **Answer from the vault.** Cite with wikilinks. Never fabricate.

## Why This Works

Small models fail when they must:
- **Pick from many tools** → solved by the three-tier system keeping the surface small for ALL models
- **Remember multi-step workflows** → solved by Route-Task's procedure chains
- **Judge safety** → solved by hiding dangerous tools from ALL models, not just small ones
- **Reason deeply** → solved by delegating to procedure cartridges (which use the big slot)

The intelligence lives in the procedures. The model is a router, not a reasoner. This is the architecture that makes "shitty laptop + free OpenRouter model = working AI" possible — without any special mode flags.

## What Died

- **`SMALL_MODEL_MODE`** — never implemented in code, now explicitly rejected. No conditional tool gating based on which model is in which slot.
- **"4 tools only" for small models** — replaced by "same tools for everyone, procedures are the path of least resistance."
- **The idea that small models need a special restricted environment** — they don't. The environment is already restricted enough for everyone.

## Falsifiability

This architecture is falsifiable if:
- Route-Task classifies a task into the wrong branch (the classifier failed)
- A procedure chain produces worse results than an unconstrained cloud model (the procedures are insufficient)
- The bootstrap contract fails to constrain behavior (the model ignores it)
- A small model in the small slot consistently fails to route correctly while a big model in the same slot succeeds (the routing task is too hard for the small model)

## Related Notes

- [[Route-Task]] — the master dispatcher implementing router-first
- [[Small-Model-Bootstrap]] — session-start orientation operationalizing this architecture
- [[Procedure-Composition-Patterns]] — the general template for conditional if-branch procedures
- [[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]] — research backing the routing approach
