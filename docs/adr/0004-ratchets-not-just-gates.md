# 0004. Debt ratchets instead of one-shot lint gates

Status: Accepted

## Context

The conventional way to keep a codebase clean is a lint gate: run ruff /
pyright / pytest in CI and fail on any violation. This works until the
codebase has *pre-existing* debt — then the gate either fails on debt you
didn't introduce (blocking all progress) or is disabled (blocking
nothing).

VaultBot inherited a large amount of pre-existing debt (hundreds of lint
violations, swallowed exceptions, type errors). A naive hard gate would
have frozen development.

## Decision

Use **ratchets**, not one-shot gates. CI measures the current count of a
given class of debt (inline SLOC, pyright errors, pytest failures) and
fails only if it *exceeds* a committed baseline in `.ci-baseline.json`.
The baseline can only move down (debt paid) or, rarely, up (new
infrastructure accepted, with justification in the same PR).

This makes debt reduction **monotonic**: it can never silently regress,
but it also never blocks unrelated work.

## Consequences

- **Easier:** debt is paid down incrementally without a big-bang cleanup,
  and new debt is caught at the moment it's introduced.
- **Harder:** the baseline file is a second source of truth that must be
  updated in the same PR as any change that moves the count. Forgetting it
  fails CI.
- **Given up:** the simplicity of "zero violations or fail." The ratchet
  tolerates a *known, bounded* amount of debt in exchange for never
  regressing.
