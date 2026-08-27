---
type: procedure
status: active
baseline: true
created: 2026-08-02
updated: 2026-08-03
record: true
superseded_by: "chat_handler.py (2026-08-02 refactor)"
summary: "HISTORICAL: Describes the old 3-phase PLAN→ACT→SYNTHESIZE state machine that was removed in the 2026-08-02 chat_handler.py refactor. Kept for project history."
tags: [agentic-loop, state-machine, working-memory, phase, turn-protocol, record, history]
when_to_use: "DEPRECATED — do not follow this protocol. The current loop is model-driven with no phases."
description: "HISTORICAL: Describes the old 3-phase PLAN→ACT→SYNTHESIZE state machine that was removed in the 2026-08-02 chat_handler.py refactor. Kept for project history."
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
allowed_tools:
  - plan_task
  - update_task
---

# Agentic Loop Turn Protocol

> **⚠ RECORD — NOT CURRENT DOCUMENTATION**
> This note describes past behavior that was **removed** in the 2026-08-02 chat_handler.py refactor. It is preserved for project history. Do not use it to understand how the system works today. The current loop is model-driven — see `chat_handler.py` docstring for current behavior.

## Why This Exists

This note exists to preserve the history of the removed 3-phase PLAN→ACT→SYNTHESIZE state machine so future work understands why it was removed. The phase state machine caused friction — the framework rejected correct model outputs and the model got stuck arguing with the phase. The key tradeoff it documents is the shift from framework-enforced phases to a model-driven loop with no gates.

## What This Was

This procedure described a mandatory 3-phase state machine (PLAN → ACT → SYNTHESIZE) that the framework enforced. The framework would track the phase, block illegal outputs, reject wrong-phase tool calls, and auto-mark steps. The LLM was required to emit only the legal output for the current phase.

## Why It Was Removed

The 2026-08-02 refactor stripped all framework-side babysitting. The chat_handler.py docstring now states:

> "The model drives. It can use plan_task / update_task to track its own state. But the framework NEVER blocks, rejects, or auto-marks anything. No phases, no gates, no forced convergence, no consolidation, no step summaries."

The phase state machine caused friction: the framework would reject model outputs that were actually correct, the model would get stuck arguing with the phase, and the rigid phase structure prevented the model from adapting to real task flow. The fix was to make the model responsible for planning, tracking, and stopping — the framework just keeps the conversation bounded and streams output.

## What Replaced It

The model now:
1. Reads the working memory block (injected every round) to see its todo list
2. Calls `plan_task` to create a plan, `update_task` to track progress
3. Calls whatever tools each step needs
4. Writes a final answer when done

No phases, no gates, no forced convergence. The model is the driver.

## Historical Reference

The original 3-phase model:

| Phase | What the framework had done | What the LLM had to do | What was illegal |
|---|---|---|---|
| **PLAN** | No plan exists yet. | Call `plan_task(goal, steps)` once. | Any other tool call. Any prose answer. |
| **ACT** | A plan exists. | Do the work, then call `update_task(completed)`. | Writing a final answer. Stopping early. |
| **SYNTHESIZE** | All steps done. | Write the final answer as prose. | Any tool calls. |

This was enforced by the framework reading structural signals (tool_calls vs. text) and returning `[FRAMEWORK REJECTION]` messages when the model emitted the wrong output type.

## Related

- [[Record-Convention]] — how historical notes are marked
- `chat_handler.py` — current implementation (docstring lines 1-20)