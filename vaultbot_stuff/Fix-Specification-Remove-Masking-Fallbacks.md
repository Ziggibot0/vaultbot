---
type: plan
status: complete
created: 2026-07-31
updated: 2026-08-11
title: "Fix Specification: Remove All Masking Fallbacks"
tags:
  - engineering
  - code-quality
  - fail-loud
  - masking
  - fallbacks
summary: Removing masking fallbacks to enforce strict fail loudly behavior ensures that missing safety checks are rejected rather than bypassed by treating errors as passing conditions.
---

# Fix Specification: Remove All Masking Fallbacks

> **<!-- updated 2026-08-11 -->** This spec was written 2026-07-31 and has been applied. Some code references have since changed:
> - `compactor.py` no longer exists — replaced by `lazy_condenser.py` (588 lines). Compaction is now a sliding window, not LLM-based.
> - Line numbers and function locations may have shifted since the original audit.
> - See `vaultbot_stuff/System/Procedures/Fix-Masking-Fallbacks.md` for the applied version.

## Law

> All code must fail loudly. No fallbacks, no silent degradation. Checking multiple sources is fine, but trying different mechanisms in a row is lazy — there will always be more edge cases. If it breaks, it breaks visibly.

## Scope

10 fixes across 7 files. 92 total masking patterns found in the codebase; these 10 are the highest-impact ones where silent failure causes the most damage.

---

## Fix 1: `self_improver.py` L400 — Pytest gate fakes a pass

**File:** `vaultbot_stuff/vaultbot_backend/self_improver.py`
**Lines:** 400–402

### Problem

When pytest can't run (not installed, import error, wrong interpreter), the safety gate sets `p_ok = True` — treating a **missing safety check as a pass**. This means a broken edit can sail through the safety gate unchecked.

### Before

```python
except Exception as e:
    checks["pytest"] = f"skipped: could not run pytest: {e}"
    p_ok = True  # treat as pass; soft gate
    p_out = None
```

### After

```python
except Exception as e:
    checks["pytest"] = f"BLOCKED: could not run pytest: {e}"
    p_ok = False  # FAIL the gate — if pytest can't run, the edit is rejected
    p_out = f"pytest could not execute: {e}"
```

### Rationale

If the safety gate can't run, the edit must be **rejected**, not allowed through. The import check is still the hard gate, but pytest is the second line of defense — disabling it silently is worse than having no second line at all. The operator sees `BLOCKED` in the checks dict and knows exactly why.

---

## Fix 2: `fused_retrieval.py` — 13 silent channel degradations

**File:** `vaultbot_stuff/vaultbot_backend/fused_retrieval.py`
**Lines:** 213, 231–234, 343–345, 378–380, 436–438, 496–498, 506, 516, 590

### Problem

Every retrieval channel (vector search, graph traversal, backlinks, drift rerank, neighbors, file resolution, content fetch) wraps its logic in `try/except Exception` and returns `[]`, `{}`, or `""` on failure. The fused retriever silently degrades — the model gets progressively worse context with zero indication that retrieval is broken.

### Before (representative — L213)

```python
except Exception as e:
    self._log("vector.error", f"{type(e).__name__}: {e}")
    return [], {}
```

### After

```python
# Remove the try/except entirely. Let exceptions propagate to the caller
# (handle_chat), which already has exception handling and user notification.
# The caller can decide whether to surface the error or degrade.
```

### Specific changes per location

| Line | Current | Fix |
|------|---------|-----|
| 213 | `return [], {}` | Remove try/except, let raise |
| 231–234 | Log + continue with raw | Remove try/except around drift rerank, let raise |
| 343–345 | `return candidates` (empty) | Remove try/except around graph traversal, let raise |
| 378–380 | `return candidates` (empty) | Remove try/except around backlink channel, let raise |
| 436–438 | `backlinks = {}` | Remove try/except around rerank graph access, let raise |
| 496–498 | `return []` | Remove try/except around `_safe_neighbors`, let raise |
| 506 | `return ""` | Remove try/except around `_file_path_for_node`, let raise |
| 516 | `return ""` | Remove try/except around `_content_for_node`, let raise |
| 590 | `pass` | Remove try/except around rerank, let raise |

### Rationale

The caller (`handle_chat`) already has a try/except around `fused_retriever.retrieve` that notifies the user. The per-channel try/excepts are redundant masking — they swallow the error before it reaches the only place that can actually tell the user something went wrong. The `_safe_neighbors` and `_file_path_for_node` methods are called from within graph traversal, so their failures should propagate up to the graph channel's caller, not be silently swallowed.

**Important:** The `_safe_neighbors` method name itself implies "never raise" — rename to `_neighbors` to signal the new contract. Same for `_safe_*` patterns elsewhere.

---

## Fix 3: `chat_handler.py` L293–323 — Retrieval fallback chain

**File:** `vaultbot_stuff/vaultbot_backend/chat_handler.py`
**Lines:** 293–323

### Problem

When fused retrieval fails, the code falls back to flat vector search. When that also fails, it returns `[]` and notifies the user. This is a **two-mechanism fallback chain** — exactly the pattern the new law prohibits.

### Before

```python
except Exception as e:
    session_logger.log_exception(e, context="fused_retriever.retrieve")
    await notify_info(svc, websocket,
        "My graph-based search hit a problem, so I'm using a "
        "simpler search. Results may be less connected.")
    try:
        results = await run_with_heartbeat(
            svc, websocket, "retrieving vault (fallback)",
            svc.vault_indexer.search, user_message, 5)
    except Exception:
        results = []
        await notify_problem(...)
```

### After

```python
except Exception as e:
    session_logger.log_exception(e, context="fused_retriever.retrieve")
    # No fallback. Surface the error to the user immediately.
    await notify_problem(svc, websocket, e,
        context={"stage": "searching the vault"},
        user_message=(
            "I couldn't search your vault for this question. "
            "I'll answer from what I know, but it may not be "
            "grounded in your notes."),
        remedy_hint="Try restarting VaultBot.")
    results = []
```

### Rationale

Trying a second search mechanism after the first fails is exactly the "try different mechanisms in a row" pattern the law prohibits. If fused retrieval is broken, the right response is to tell the user immediately, not silently degrade to a worse search that might also be broken. The user can judge the answer better knowing retrieval failed entirely than thinking they got "less connected" results.

---

## Fix 4: `knowledge_curriculum.py` — 14 silent failures returning `[]`

**File:** `vaultbot_stuff/vaultbot_backend/knowledge_curriculum.py`
**Lines:** 405, 446–448, 568–570, 606, 636, 706, 795, 823

### Problem

Gap detection fails silently at 14 points, each returning `[]`. The system thinks there are no knowledge gaps when really it just couldn't detect them. This is likely why stale gaps persist — the curriculum keeps failing to find new ones.

### Before (representative — L405–406)

```python
try:
    self.graph.refresh()
except Exception as e:
    self._log_error("graph_refresh", e)
    return []
```

### After

```python
# Remove try/except. If graph refresh fails, the exception propagates
# to propose_next_gaps() which should surface the error to the caller.
self.graph.refresh()
```

### Specific changes per location

| Line | Current | Fix |
|------|---------|-----|
| 398–399 | `except Exception: pass` (cache hit) | Keep — cache read failure is non-critical |
| 405–406 | `return []` (graph refresh) | Remove try/except, let raise |
| 446–448 | `return []` (propose_next_gaps) | Remove try/except, let raise |
| 477–478 | `_log_error("mark_completed", e)` | Remove try/except, let raise |
| 525–526 | `_log_error("mark_failed", e)` | Remove try/except, let raise |
| 547–548 | `return {"error": str(e)}` (state_summary) | Remove try/except, let raise |
| 568–570 | `dangling = []` (dangling_links) | Remove try/except, let raise |
| 606 | `return []` (collect_dangling_links) | Remove try/except, let raise |
| 636 | `return []` (collect_thin_notes) | Remove try/except, let raise |
| 706 | `return []` (collect_missing_entities) | Remove try/except, let raise |
| 795 | `return []` (collect_thin_communities) | Remove try/except, let raise |
| 823 | `return []` (collect_link_density) | Remove try/except, let raise |

### Rationale

If gap detection fails, the autonomous researcher can't find work to do, and the system reports stale gaps forever. The caller (`autonomous_researcher._cycle`) should know that gap detection broke — it's a critical failure, not a "no gaps found" result.

---

## Fix 5: `autonomous_researcher.py` — 11 log-only failures

**File:** `vaultbot_stuff/vaultbot_backend/autonomous_researcher.py`
**Lines:** 438–443, 467–469, 572–573, 598–600, 616–617, 631, 661, 678

### Problem

Checkpoint save, consolidation, and promotion cycle all fail silently with log-only except blocks. The background researcher could be completely broken and you'd only know from log files nobody reads.

### Before (representative — L572–573)

```python
except Exception as e:
    self._log("checkpoint_save_failed", {"error": str(e)})
```

### After

```python
# Remove try/except. If checkpoint save fails, the cycle should abort
# — the researcher can't resume after restart without checkpoints.
```

### Specific changes per location

| Line | Current | Fix |
|------|---------|-----|
| 438–443 | Log + continue (consolidation template) | Remove try/except, let raise — skips this cluster |
| 467–469 | Log + return error dict (consolidation) | Keep — this already returns `{"ok": False, "error": str(e)}` which is a proper error surface |
| 572–573 | Log-only (checkpoint save) | Remove try/except, let raise — checkpoint failure means no resume |
| 598–600 | Log-only (promotion cycle) | Remove try/except, let raise — promotion failure means stale procedures |
| 616–617 | Log-only (checkpoint clear) | Remove try/except, let raise |
| 631 | Log-only (history append) | Keep — history is diagnostic, not critical |
| 661 | `pass` (heartbeat callback) | Keep — heartbeat is best-effort by design |
| 678 | `pass` (cleanup during shutdown) | Keep — shutdown cleanup must not raise |

### Rationale

Checkpoint save failure means the researcher can't resume after a restart — it starts over from scratch. That's a critical failure that should abort the cycle, not be swallowed. Promotion cycle failure means procedures never get graded — also critical. The heartbeat callback and shutdown cleanup are genuinely best-effort (they run during teardown where raising would make things worse).

---

## Fix 6: `compactor.py` L169 — Compaction failure returns original messages

**File:** `vaultbot_stuff/vaultbot_backend/compactor.py`
**Lines:** 169–175

### Problem

If compaction fails, it returns the **full uncompacted message list**. Context grows unbounded until it hits the token limit, but the system thinks compaction is working. This is a silent OOM waiting to happen.

### Before

```python
except Exception as exc:
    logger.error("compaction failed, returning original messages: %s", exc)
    try:
        self._log_exc(exc, context="compact")
    except Exception:
        pass
    return messages
```

### After

```python
except Exception as exc:
    logger.error("compaction failed: %s", exc)
    self._log_exc(exc, context="compact")
    raise  # Let the caller handle it — never silently return unbounded context
```

### Rationale

Returning the original messages on compaction failure means the context window grows until the LLM rejects it. The caller (`handle_chat`) should know compaction broke so it can surface the error. The nested `try/except: pass` around `_log_exc` is also masking — if logging itself fails, that's a diagnostic failure worth knowing about.

---

## Fix 7: `chat_handler.py` L1288 + L1637 — Checkpoint and history persist silently fail

**File:** `vaultbot_stuff/vaultbot_backend/chat_handler.py`
**Lines:** 1288, 1637

### Problem

If saving the chat checkpoint fails, it logs and continues. After a restart, I can't resume — I lose my place mid-task. If saving chat history fails, the conversation is lost. Both are the "dying mid-task" problem.

### Before (L1288)

```python
except Exception as e:
    session_logger.log("chat_checkpoint_save_failed", {"error": str(e)})
```

### After (L1288)

```python
except Exception as e:
    session_logger.log("chat_checkpoint_save_failed", {"error": str(e)})
    # Surface to user — they need to know their session won't survive a restart
    await notify_problem(svc, websocket, e,
        context={"stage": "saving checkpoint"},
        user_message=(
            "I couldn't save my progress checkpoint. If I restart, "
            "I won't be able to resume this task. Your chat still works."),
        remedy_hint="Check disk space and file permissions.")
```

### Before (L1637)

```python
except Exception as e:
    session_logger.log("history_persist_failed", {"error": str(e)})
```

### After (L1637)

```python
except Exception as e:
    session_logger.log("history_persist_failed", {"error": str(e)})
    # Surface to user — they need to know their conversation won't survive a restart
    await notify_problem(svc, websocket, e,
        context={"stage": "persisting chat history"},
        user_message=(
            "I couldn't save our conversation history. If I restart, "
            "I won't remember this chat."),
        remedy_hint="Check disk space and file permissions.")
```

### Rationale

These aren't "remove the try/except" fixes — the chat should continue even if persistence fails. But the **user must be told** that their session won't survive a restart. Silent failure here is the difference between "I'll pick up where I left off" and "I have no idea what we were doing."

---

## Fix 8: `main.py` L410 — Researcher crash notification silently passes

**File:** `vaultbot_stuff/vaultbot_backend/main.py`
**Lines:** 410–411

### Problem

If the autonomous researcher crashes, the notification that's supposed to tell you about it can itself silently fail. You'd have no idea the researcher died.

### Before

```python
except Exception:
    pass  # the callback must never raise
```

### After

```python
except Exception as notify_err:
    # The crash notification itself failed — log it loudly.
    # This is the one place where we MUST not silently pass:
    # if the user doesn't know the researcher crashed, they think
    # the vault is being maintained when it isn't.
    default_session_logger.log_exception(
        notify_err, context="autonomous_researcher_crash_notification")
    print(f"[CRITICAL] Researcher crashed AND crash notification failed: {notify_err}")
```

### Rationale

The callback shouldn't raise (it's called from a thread), but `pass` means total silence. At minimum, log the notification failure so it shows up in diagnostics. The `print` is a belt-and-suspenders guarantee that it's visible even if the session logger is broken.

---

## Fix 9: `llm_client.py` — Pass-only excepts on logging

**File:** `vaultbot_stuff/vaultbot_backend/llm_client.py`
**Lines:** 185–186, 194–195

### Problem

Two `except Exception: pass` blocks swallow logging failures silently. If the session logger is broken, you lose all LLM call diagnostics with no indication.

### Before (L185)

```python
except Exception:
    pass
```

### After (L185)

```python
except Exception as log_err:
    # Logging failed — at least print so it's visible in the console
    print(f"[WARN] session_logger.log_tool_call failed: {log_err}")
```

### Before (L194)

```python
except Exception:
    pass
```

### After (L194)

```python
except Exception as log_err:
    print(f"[WARN] session_logger.log('model_changed') failed: {log_err}")
```

### Rationale

These are logging-of-logging failures, so they're low severity. But `pass` is still silent. A `print` ensures the failure is at least visible in the console. We don't raise because logging failures shouldn't break the LLM client.

**Note:** `list_models()` L205 returning `[]` and `health_check()` L281 returning `False` are **correct** — they're query methods where returning an empty/false result IS the proper signal. These are NOT masking fallbacks.

---

## Fix 10: `chat_handler.py` remaining log-only excepts — audit and classify

**File:** `vaultbot_stuff/vaultbot_backend/chat_handler.py`
**Lines:** Multiple (see table)

### Problem

42 broad except blocks in chat_handler.py. Not all are masking — some are genuinely best-effort (heartbeat, partial cleanup, drift feedback). Need to classify each.

### Classification

| Line | Pattern | Verdict | Action |
|------|---------|---------|--------|
| 240 | correction detection failed | **Masking** | Surface to user — they should know calibration isn't working |
| 293 | graph refresh failed | **Masking** | Remove try/except, let raise — retrieval needs a fresh graph |
| 343 | procedure surface failed | **Best-effort** | Keep — procedures are optional context |
| 361 | note_touched failed | **Best-effort** | Keep — tracking is diagnostic |
| 413 | budget logging failed | **Best-effort** | Keep — logging failure in logging |
| 494 | procedure compile failed | **Masking** | Surface — broken procedures mean no procedural context |
| 683 | plan mode directive failed | **Masking** | Surface — plan mode is a core feature |
| 701 | plan mode append failed | **Masking** | Surface — same as above |
| 793 | tool chain setup failed | **Masking** | Surface — tools are core |
| 1052 | tool dispatch failed | **Masking** | Already raises — verify |
| 1288 | checkpoint save failed | **Masking** | Fix 7 |
| 1304 | partial cleanup failed | **Best-effort** | Keep — cleanup is non-critical |
| 1323 | context usage emit failed | **Best-effort** | Keep — telemetry |
| 1341 | session logger failed | **Best-effort** | Keep — logging of logging |
| 1384 | vault_changed failed | **Masking** | Surface — broadcast failure means UI doesn't update |
| 1424 | drift feedback failed | **Best-effort** | Keep — drift is a bonus, not critical |
| 1454 | post-condense linkout failed | **Best-effort** | Keep — post-processing |
| 1475 | post-condense crosslink failed | **Best-effort** | Keep — post-processing |
| 1493 | post-condense card rebuild | **Best-effort** | Keep — post-processing |
| 1526 | card rebuild failed | **Best-effort** | Keep — cards are derived |
| 1534 | drift reset failed | **Best-effort** | Keep — drift is bonus |
| 1564 | drift reset failed | **Best-effort** | Keep — same |
| 1571 | card refine failed | **Best-effort** | Keep — refinement is bonus |
| 1574 | lazy condense bg failed | **Best-effort** | Keep — background task |
| 1637 | history persist failed | **Masking** | Fix 7 |
| 1645 | chat note creation failed | **Masking** | Surface — user loses their chat log |
| 1698 | self-model regen failed | **Masking** | Surface — self-model is core identity |
| 1719 | goals update failed | **Masking** | Surface — goals are critical for continuity |
| 1764 | subagent import failed | **Masking** | Surface — research subagent is a feature |
| 1777 | subagent dispatch failed | **Masking** | Surface — same |
| 1787 | subagent result failed | **Best-effort** | Keep — result processing is post-hoc |
| 1826 | post-answer processing | **Best-effort** | Keep — post-processing |
| 1941 | distillation failed | **Best-effort** | Keep — distillation is background |

### Action

For each row marked **Masking**: replace `session_logger.log(...)` with `await notify_problem(...)` to surface the error to the user. For **Best-effort** rows: keep the try/except but add a `print()` so failures are at least visible in the console.

---

## Implementation Order

1. **Fix 1** (self_improver pytest gate) — smallest, highest risk, do first
2. **Fix 6** (compactor) — small, high impact
3. **Fix 8** (main.py crash notification) — small, clear
4. **Fix 9** (llm_client logging) — small, clear
5. **Fix 7** (chat_handler checkpoint/history) — medium, needs notify_problem
6. **Fix 3** (chat_handler retrieval fallback) — medium, needs notify_problem
7. **Fix 5** (autonomous_researcher) — medium, need to be careful about which to keep
8. **Fix 4** (knowledge_curriculum) — large, 12 locations
9. **Fix 2** (fused_retrieval) — large, 9 locations, need to rename _safe_* methods
10. **Fix 10** (chat_handler audit) — largest, needs per-case judgment

## Testing

After each fix:
1. Run `python -c "import <module>"` to verify the file still imports
2. Run pytest on the backend directory
3. Restart the backend and verify chat still works
4. Verify the autonomous researcher still cycles

## Files Modified

- `vaultbot_stuff/vaultbot_backend/self_improver.py` (Fix 1)
- `vaultbot_stuff/vaultbot_backend/fused_retrieval.py` (Fix 2)
- `vaultbot_stuff/vaultbot_backend/chat_handler.py` (Fixes 3, 7, 10)
- `vaultbot_stuff/vaultbot_backend/knowledge_curriculum.py` (Fix 4)
- `vaultbot_stuff/vaultbot_backend/autonomous_researcher.py` (Fix 5)
- `vaultbot_stuff/vaultbot_backend/compactor.py` (Fix 6)
- `vaultbot_stuff/vaultbot_backend/main.py` (Fix 8)
- `vaultbot_stuff/vaultbot_backend/llm_client.py` (Fix 9)