---
type: pattern
tags: [meta, framework, bugfix, chat_handler]
created: 2026-07-31
---

# Framework Stop Mechanism Fix

## The Problem

VaultBot was dying mid-task with the message "I gathered information but was unable to synthesize a complete response." This happened 10+ times across sessions and was the #1 operator complaint.

## Root Cause

The previous "Remove-All-Stops" fix had a **backwards bug**. It set `_force_synthesize_nudged = True` thinking it would disable the force-synthesize nudge. It DID disable the nudge (Check 3: `if not _force_synthesize_nudged` → `not True = False`), but it ALSO **enabled the ultimate fallback** (Check 4: `if _force_synthesize_nudged` → `True`).

### The death sequence (before fix):

1. Model goes empty → Check 1 nudges "write something" → continue
2. Model goes empty again → Check 2 nudges "keep working on your plan" → continue
3. Model goes empty again → Check 3 (`if not _force_synthesize_nudged`) → **DISABLED** (True, so `not True = False`)
4. Falls through to Check 4 (`if _force_synthesize_nudged`) → **FIRES** (True) → generates fake "unable to synthesize" message → **`break`**

The model only got 2 nudges before being killed. The third nudge was disabled, and the fallback that was supposed to be disabled was actually enabled.

### Second bug: `final_answer = round_text`

When the model went empty on the final round, `final_answer = round_text` **overwrote** all accumulated text from previous rounds with the empty string. So even if the model had produced useful text in earlier rounds, it was lost.

## The Fix (4 changes in `chat_handler.py`)

| # | Line | Before | After | Why |
|---|------|--------|-------|-----|
| 1 | 612 | `_force_synthesize_nudged = True` | `_force_synthesize_nudged = False` | Re-enable the force-synthesize nudge (Check 3) |
| 2 | 898 | `_force_synthesize_nudged = False` | `_force_synthesize_nudged = True` | Mark nudge as fired (1-shot, so it only fires once) |
| 3 | 923 | `if not round_text.strip() and _force_synthesize_nudged:` | `if False and not round_text.strip() and _force_synthesize_nudged:` | Disable the ultimate fallback — never generate fake "unable to synthesize" message |
| 4 | 948 | `final_answer = round_text` | `if round_text.strip(): final_answer = round_text` | Don't overwrite accumulated text with empty string |

### New flow (after fix):

1. Model goes empty → Check 1 nudges → continue
2. Model goes empty → Check 2 nudges (if plan incomplete) → continue
3. Model goes empty → Check 3 nudges "write a response NOW" → continue
4. Model goes empty → All checks exhausted → `if round_text.strip(): final_answer = round_text` (keeps accumulated text) → **`break`** with whatever was accumulated

The model now gets **3 nudges** before the turn ends, and the turn ends with **accumulated text** instead of a fake message.

## What Was NOT Changed

- **Round caps**: Already removed (`while True:` loop) ✅
- **Read-only loop detector**: Already disabled (`False and` + `_MAX_READ_ONLY_STREAK = 999999`) ✅
- **Plan gate enforcement**: Still active (`_FORCE_PLAN_ON_MULTI`, 5 rounds without plan → gate activates). This is a nudge, not a hard stop.
- **code_read dedup notice**: Still active. Appends a notice after reading the same file multiple times. This is a nudge, not a hard stop.

## Related

- [[Autonomy-Interruption-and-Goal-Staleness-Pattern]]
- [[Chat-remove-stops-no-stops]]
- [[Chat-whyd-you-stop-whats-wrong-with-your-framework-t]]