---
type: concept
status: active
created: 2026-08-03
tags:
  - architecture
  - small-models
  - procedures
  - routing
  - obsolescence
summary: Design standard for making VaultBot drivable by weak models — minimal visible toolset, router-first dispatch, code-step-heavy deterministic procedures, cartridge pinning, and safety gating inside procedures.
---

# Small-Model-Driving-Architecture

## Claim

VaultBot must be drivable by the weakest model that can still emit one valid tool call. The vault — its notes and procedures — carries the cognition; the model only routes intent. The target user is a random person on a cheap laptop who puts a free OpenRouter model in the big-model slot and gets a working AI at their fingertips. That is only possible because the intelligence lives in the vault instead of the weights — the same thesis as the cloud-model obsolescence mission. This note is its concrete design standard.

## Why small models fail today

Inspection of `vaultbot/vaultbot_backend/agent_tools.py` (the three-tier tool system) shows the current surface is still too large and too complex:

1. **Surface area.** Tier 1 sends 8 core tools every turn (`vault_search`, `vault_read_note`, `code_read`, `plan_task`, `update_task`, `execute_procedure`, `vault_safe_write`, `vault_append`). Tier 2 adds up to ~16 contextual tools via `select_contextual_tools()` keyword matching. Broad keywords (`write`, `note`, `tool`, `system`, `code`) fire on almost any message, so an ordinary turn advertises 20–24 schemas. A small model cannot reliably pick from 24 schemas; worse, the failure is silent — it picks a plausible-but-wrong tool and the loop burns rounds recovering.
2. **Schema complexity.** `execute_procedure` takes a free-form `args` dict; `safe_write` takes an entire file body; `plan_task` takes an array of step strings. Small models truncate long arguments, hallucinate keys, and emit malformed JSON under schema pressure.
3. **Loop discipline is assumed, not enforced.** The plan → act → update_task protocol requires multi-turn diligence that weak models do not have. They forget to update, stop mid-plan, or re-plan every turn.
4. **Error recovery needs judgment.** When a tool errors, a small model retries identically or abandons the plan. Deterministic procedures must absorb errors instead of delegating recovery to the model.

## The five design rules

1. **Router-first.** The only decision a small model makes is *which procedure handles this intent*, via [[Route-Task]]. Everything after routing is deterministic code. Routing is a classification task — the easiest thing a small model can do reliably.
2. **Minimal visible toolset.** In small-model mode the model sees at most 4 tools: `vault_search`, `vault_read_note`, `execute_procedure`, `plan_task`. No raw edit tools, no `safe_write`, no `tool_create`, no delete or restart primitives. Implementation hook: a `SMALL_MODEL_MODE` flag in `agent_tools.py::build_tool_list` that skips Tier-2 keyword expansion entirely.
3. **Code-step-heavy procedures.** Work happens in `code` steps at zero LLM cost. LLM steps exist only for judgment calls, use tight structured prompts, and demand few-token outputs (a name, a yes/no, a short list) that weak models produce reliably.
4. **Cartridge pinning.** Every procedure declares `model_cartridge` per step, defaulting to `small`. The big cartridge is reserved for genuinely hard synthesis — and small-model mode treats even that as optional.
5. **Safety gating inside procedures, never as raw tools.** Dangerous capabilities (self-edit, note deletion, backend restart) live inside procedures that run preflight checks, dry-run first, and back up before writing. A small model can *request* a dangerous operation only by routing to its procedure; it can never fire the primitive directly. [[Check-Tool-Coverage]] is the existing example of the pattern: assessment happens inside a procedure, not via raw tool inventory.

## What this changes

- `agent_tools.py`: add the `SMALL_MODEL_MODE` gate (rule 2).
- [[Route-Task]]: upgrade to the canonical front door — a tight structured prompt that maps intent → procedure name + args, executable by a small model (rule 1).
- [[Small-Model-Bootstrap]]: new orientation procedure run at session start, telling the model exactly what to do and what never to touch raw (rules 2 and 5).

## Connections

- [[Sliding-Window-Conversation-Trail-Tools-as-Procedures-Spec]] — the tier system this note constrains further. That spec moved tools into procedures; this standard shrinks what remains visible to the model.
- [[Route-Task]] — the router that becomes the small model's front door.
- [[Check-Tool-Coverage]] — the existing capability-assessment procedure; small-model mode routes through procedures like this instead of exposing raw tools.
