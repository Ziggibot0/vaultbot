---
type: plan
status: complete
baseline: true
created: 2026-07-30
completed: 2026-07-30
updated: 2026-08-03
record: true
title: Framework Friction Fix Plan
tags:
  - plan
  - framework
  - self-improvement
summary: "Framework Friction Fix Plan: 102 chars"
---

# Framework Friction Fix Plan

> **📋 RECORD** — This is a completed plan from 2026-07-30. It is preserved for project history. The fixes described here have been implemented. See current code for how the system actually works.

## Root Cause Analysis

Three groups of issues share underlying root causes:

### Group A: Code-First Safety Bias (FR-1, FR-6)
**Root cause:** The safety framework was built code-first. safe_write validates Python, js_safe_write validates JS, but markdown — the primary output — had no safety layer. This inverted the vault thesis: knowledge should be MORE protected than code, not less.

**Fix (DONE):** Built vault_safe_write — a markdown-safe write tool that:
- Backs up the existing file to trash/ before overwriting (if file exists)
- Validates the content is non-empty markdown
- Blocks LOCKED notes, sacred journals, and path traversal
- Writes atomically (write to temp, then rename)
- Returns the diff size and backup path

### Group B: Context Mismanagement (FR-2, FR-3, FR-5)
**Root cause:** The framework doesn't manage context as a scarce resource at the tool-result level. context_budgeter.py governs system prompt injection but not tool result truncation, search relevance filtering, or session persistence.

**Fixes (ALL DONE):**
- FR-2 (truncation): Increased truncate_tool_result default from 6000 to 10000 chars. Truncation messages now report which keys were truncated, how many chars were dropped, original size vs cap, and a hint to re-read with narrower parameters.
- FR-3 (search noise): Added MIN_SCORE_THRESHOLD = 0.15 to FusedRetriever. Results below this normalized score are dropped after reranking. Fallback keeps top result if all fall below threshold.
- FR-5 (session clears): Added optional `context` parameter to set_goal tool. Written to GOALS.md as a "## Current Goal Context" section so context persists across restarts.

### Group C: Babysitting Overhead (FR-4, FR-7)
**Root cause:** The framework was doing too much hand-holding: phase state machine enforcement, forced convergence, consolidation summaries, auto-marking steps. This added complexity without improving output quality. The model should drive; the framework should just keep the conversation bounded and stream output.

**Fixes (ALL DONE):**
- FR-4 (phase machine): Removed the 3-phase PLAN→ACT→SYNTHESIZE state machine. The model now drives the loop freely. See chat_handler.py docstring (2026-08-02).
- FR-7 (consolidation): Removed consolidation summaries. The model sees its full conversation history (bounded by sliding window) instead of compressed step summaries.

## Status

All fixes implemented. This plan is complete and preserved for history.