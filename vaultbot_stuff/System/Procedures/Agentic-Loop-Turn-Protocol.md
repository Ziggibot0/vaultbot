---
type: procedure
status: active
created: 2026-08-02
model_cartridge: small
summary: "The mandatory turn-by-turn state machine for VaultBot's agentic chat loop. The framework sets the phase; the LLM obeys it. Following this prevents the 'lost in the loop' failures where the model plans, acts, or synthesizes at the wrong time."
tags: [agentic-loop, state-machine, working-memory, phase, turn-protocol]
when_to_use: "every chat turn, before emitting any text or tool call, to determine which actions are legal in the current framework phase"
---

# Agentic Loop Turn Protocol

This procedure is **not optional**. It is the contract between the framework and the LLM. The framework tracks the phase in working memory and prints the current phase at the start of every turn. The LLM must read that phase and emit **only** the legal output for that phase.

## Why this exists

VaultBot was getting stuck in its agentic loop: the user would ask for a GUI change, the framework would set a plan, but the LLM would read files forever, emit silence, or write prose instead of closing out steps. The root cause was that the phase rules were buried inside a long system prompt. This note makes them explicit, first-class, and retrievable.

## The three phases (one and only one is active per turn)

```
PLAN  →  ACT  →  SYNTHESIZE
```

| Phase | What the framework has done | What the LLM must do now | What is illegal |
|---|---|---|---|
| **PLAN** | No plan exists yet for this user request. | Call `plan_task(goal, steps)` once. The framework will then move to ACT automatically. | Any other tool call. Any prose answer. Asking the user questions. |
| **ACT** | A plan exists in working memory. The framework has auto-marked the current step `[>]` in_progress. | 1. **Do the work for the current step** using the tools it needs (e.g. `code_read`, `vault_search`, `code_run`).<br>2. When the step is verifiably done, **call `update_task(task_id, status="completed")`**. The framework consolidates the step and auto-starts the next one.<br>Repeat until no `[ ]` steps remain. | Prose answers. Calling `plan_task` again unless you intend to replace the whole plan. Stopping before every step is marked `[x]`. |
| **SYNTHESIZE** | Every step in the plan is marked `[x]`. | Write the final answer to the user in prose. No tools. No extra research unless the user asked a follow-up. | Any tool call. Marking more tasks. Starting new work. |

## Reading the phase at the start of every turn

The framework injects exactly one of these lines at the top of the system prompt:

```
[FRAMEWORK PHASE: PLAN]
[FRAMEWORK PHASE: ACT]
[FRAMEWORK PHASE: SYNTHESIZE]
```

The LLM must look for that line before it emits anything. The phase is the single source of truth for what the LLM is allowed to do this turn.

## Working memory format (what the LLM sees)

```
# WORKING MEMORY (your active plan)
Goal: <one sentence>
[ ] 1. Step one
[>] 2. Step two (current)
[x] 3. Step three
Progress: 1/3 done
  ↳ Step 1 summary: <gist of what was accomplished>
```

- `[ ]` pending — not started
- `[>]` in_progress — the step you are currently working on (framework auto-marks this)
- `[x]` completed — already finished and summarized

## ACT-phase step lifecycle (the most common failure mode)

1. **The framework picks the next pending step.** It auto-marks it `[>]` in_progress. You never call `update_task(status="in_progress")`.
2. **Do the work.** Call whatever tools the step needs: `code_read`, `vault_search`, `code_run`, `safe_write`, `vault_lint`, etc.
3. **Close the step.** When the step is verifiably done, call `update_task(task_id, status="completed")` with a short `notes` field if useful. The framework consolidates the step and auto-starts the next one.
4. **Loop.** If more `[ ]` steps remain, return to step 1.

If a step fails, mark it `completed` with `notes="blocked by X"` and move on. Do not get stuck re-explaining the failure.

## What to do if the phase feels wrong

The framework sets the phase from working memory. Do not argue with the phase in prose. If the phase is wrong, it is because the task list is wrong. Fix the task list using `update_task` or `plan_task`, which will move the framework into the correct phase automatically.

- If you think you should still be planning but the phase is ACT: call `plan_task` to replace the plan, which resets to ACT with the new plan (framework plans once at turn start; a new `plan_task` during ACT replaces the plan).
- If you think you should already be done but the phase is ACT: mark the remaining steps `[x]` completed (honestly).
- If the phase is SYNTHESIZE but you are not ready: that means every step is already marked done; you must synthesize anyway. Record blockers in the final answer, do not start new work.

## Why this prevents looping

The previous failures happened because the LLM treated the loop like a free-form conversation. It is not. Each turn has a single legal output type:

- PLAN → one `plan_task` call.
- ACT → alternating work tools + one `update_task(..., "completed")` when the step is done.
- SYNTHESIZE → prose final answer.

When the LLM emits the wrong output for the phase, the framework rejects it and returns a `[FRAMEWORK REJECTION]` message. If the LLM keeps ignoring the rejection, the loop thrashes. The fix is to read the phase line first, every turn, and emit only the legal output.
