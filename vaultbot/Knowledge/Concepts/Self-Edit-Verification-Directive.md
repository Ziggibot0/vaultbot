---
type: concept
status: active
created: 2026-08-03
tags:
  - concept
  - self-edit
  - verification
  - safety
  - code-quality
summary: Self-Edit-Verification-Directive
---

# Self-Edit-Verification-Directive

## Claim

Every self-edit to VaultBot's backend code must be verified immediately after writing, before any other action is taken. The verification must confirm that the edited file is syntactically valid, importable, and does not break existing functionality. No edit is considered "done" until verification passes.

## Reasoning

Self-modifying AI systems face a unique risk: an edit that breaks the system's own ability to function also breaks its ability to detect or fix the break. This creates an unrecoverable failure mode — the system can't self-repair because the repair mechanism itself is damaged.

The directive establishes a **verification gate** between every edit and the next action:

1. **Write** — use [[Safe-Write]] to edit the file (syntax check + atomic write)
2. **Verify** — use [[Proc-Step-Summary]] to import the file in a subprocess and check for errors
3. **Confirm** — if verification passes, proceed; if it fails, rollback or fix before continuing

This three-step pattern ensures that the system never enters a broken state without knowing it. The [[Safe-Write]] procedure already implements syntax checking and auto-rollback for import failures, but the directive extends this by requiring an *explicit* verification step that produces a report — not just a silent pass/fail.

## Evidence from Research

- The [[AI-system-audit-categories-how-to-audit-an-AI-agent-system-for-reliability-knowl]] research identifies "tool safety" as a critical audit dimension: autonomous agents that can modify their own code must have verification gates to prevent self-inflicted damage
- [[Deterministic-Scaffolding-for-Small-Models]] argues that deterministic checks (like import verification) are more reliable than model-based judgments for safety-critical operations
- The [[Execution-Loop-Dominance-Pattern]] supports this: the verification step is the "observe" phase of the act-observe-adjust loop — without it, the loop is blind

## Implementation in VaultBot

The directive is implemented through procedure composition:

```
Self-Edit-Verification-Directive
  ├── Safe-Write (write + syntax check + auto-rollback)
  ├── Proc-Step-Summary (import verification in subprocess)
  └── If verification fails → diagnose error → fix or rollback
      If verification passes → proceed to next task
```

The [[Safe-Write]] procedure handles the write phase with:
- UTF-8 encoding
- Syntax check via `compile()`
- For core modules: full import test in a subprocess
- Auto-rollback if import fails

The [[Proc-Step-Summary]] procedure handles the verify phase with:
- Subprocess import of the edited file
- Error reporting with traceback
- No backend restart (non-destructive check)

## Why This Matters

Without this directive, self-edits could introduce subtle bugs that only manifest later — when the system tries to use a broken function, it fails in a way that's hard to trace back to the edit. The verification gate catches problems at the earliest possible moment, when the context of what changed is still fresh.

This is especially important for [[Route-Task]] and other procedures that compose multiple sub-procedures — a broken sub-procedure could cascade failures through the entire chain.

## Related

- [[Safe-Write]] — the write procedure
- [[Proc-Step-Summary]] — the verification procedure
- [[AI-system-audit-categories-how-to-audit-an-AI-agent-system-for-reliability-knowl]] — audit framework
- [[Execution-Loop-Dominance-Pattern]] — why observation matters
- [[Deterministic-Scaffolding-for-Small-Models]] — deterministic checks over model judgment