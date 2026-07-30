---
type: plan
status: complete
created: 2026-07-30
completed: 2026-07-30
title: Framework Friction Fix Plan
tags: [plan, framework, self-improvement]
---

# Framework Friction Fix Plan

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
- FR-5 (session clears): Added optional `context` parameter to set_goal tool. Written to GOALS.md as a "## Current State" section (capped at 500 chars). Survives session clears. Updated identity.py, identity_api.py, agent_tools.py, and chat_handler.py end-to-end.

### Group C: Rigid Control Flow (FR-4)
**Root cause:** The framework assumes linear plan→execute→synthesize. Real work often requires explore→understand→plan→execute.

**Fix (DONE):** Increased _MAX_TOOL_ROUNDS_NO_PLAN from 3 to 5 (gives 5 rounds of exploration before forcing plan mode). Updated plan_mode_directive to explicitly encourage exploration — "Take as many exploration rounds as you need, then call plan_task when you understand the problem well enough."

## Fix Order (all completed)

1. ✅ **FR-1/FR-6: vault_safe_write** — built, tested all safety paths, backup system saved Dream-Pass.md during testing
2. ✅ **FR-3: search relevance** — added MIN_SCORE_THRESHOLD to FusedRetriever
3. ✅ **FR-2: truncation** — increased cap, informative truncation messages
4. ✅ **FR-5: session clears** — added context parameter to set_goal
5. ✅ **FR-4: plan enforcement** — increased threshold, updated directive

## Files Modified

- `vaultbot_backend/custom_tools/vault_safe_write.py` — NEW (markdown safe write tool)
- `vaultbot_backend/chat_helpers.py` — improved truncation (10K cap, informative messages)
- `vaultbot_backend/fused_retrieval.py` — added MIN_SCORE_THRESHOLD + filtering
- `vaultbot_backend/chat_handler.py` — increased _MAX_TOOL_ROUNDS_NO_PLAN to 5, pass context to set_goal
- `vaultbot_backend/plan_gate.py` — updated directive to encourage exploration
- `vaultbot_backend/agent_tools.py` — added context parameter to set_goal schema
- `vaultbot_backend/identity.py` — update_goals accepts context, writes Current State section
- `vaultbot_backend/identity_api.py` — passes context through
- `vaultbot_backend/custom_tools/preflight_safety_check.py` — updated folder checks
- `vaultbot_backend/vault_indexer.py` — added on_moved handler (earlier today)
- `Memory/Build-Log/README.md` — updated project structure, added new modules/tools
- `User/VaultBot Issues.md` — all issues checked off with fix descriptions

## Links
- [[VaultBot Issues]] — the issues list (all checked off)
- [[VaultBot-Is-the-Vault]] — why knowledge safety matters more than code safety
- [[context_budgeter]] — existing context management (system prompt level)
- [[fused_retrieval]] — the retrieval system (now with threshold filtering)