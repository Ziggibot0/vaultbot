# Architecture Decision Records

This directory records the *deliberate* architectural decisions that make
VaultBot different from a conventional codebase. Each record answers three
questions: **what** we chose, **why**, and **what we rejected**.

VaultBot intentionally departs from several industry norms. Those
departures are not accidents — they are the point of the project. An ADR
exists so a reviewer who knows the "standard" way can see, in one place,
that we considered it and chose otherwise on purpose.

## Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-vault-is-the-knowledge-base.md) | The vault is the knowledge base, not the model | Accepted |
| [0002](0002-procedures-not-code.md) | Procedures over inline code (the thin backend) | Accepted |
| [0003](0003-small-model-first.md) | Small local model first, cloud as fallback | Accepted |
| [0004](0004-ratchets-not-just-gates.md) | Debt ratchets instead of one-shot lint gates | Accepted |
| [0005](0005-two-account-pr-split.md) | Two-account PR author/approve split | Accepted |

## How to add an ADR

1. Copy the template below into `NNNN-short-title.md` (next number).
2. Fill in Context, Decision, and Consequences.
3. Add a row to the index table above.
4. Open a PR — ADRs are reviewed like code.

```markdown
# NNNN. Short title

Status: Proposed | Accepted | Superseded by [NNNN](NNNN-....md)

## Context

What problem are we solving? What is the "standard" way, and why is it
insufficient here?

## Decision

What we chose, in one or two sentences.

## Consequences

What gets easier, what gets harder, and what we're giving up.
```
