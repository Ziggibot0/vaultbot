---
type: claim
status: raw
created: 2026-08-03
summary: "# Baseline directives

| File | What it does |
------|-------------|
| `Autonomy-Directive.md` | Tells VaultBot to act on its own — store, organize, research, self-improve — without asking permission "
tags:
  - claim
  - baseline
---

# Baseline directives

These are **starter templates** for the directives that shape how VaultBot
behaves. They are NOT active — copy the ones you want into your vault root
(as `.md` files) and edit them to fit how *you* want your VaultBot to work.

VaultBot reads any `.md` file at the vault root as part of its context, so
once you copy a directive in, it takes effect on the next chat turn.

## What's here

| File | What it does |
|------|-------------|
| `Autonomy-Directive.md` | Tells VaultBot to act on its own — store, organize, research, self-improve — without asking permission each time. Report after the fact. |
| `Vault-Knowledge-Only-Directive.md` | The vault is the ONLY knowledge source. VaultBot never references training data. If the vault has nothing, it says "I don't know" and offers to research. |
| `IDK-Fallback-Directive.md` | Resolves the edge case: vault is empty AND research is down → "I don't know." No hedging, no training-data leakage. |
| `Communication-Preferences.md` | A template for telling VaultBot how YOU like to be talked to. Fill it in with your style. |

## The philosophy

VaultBot ships **curious, not opinionated**. It doesn't assume you want
autonomy, or that you hate Wikipedia, or that you like bullet points. It
starts neutral and learns from you. These templates let you set the rules
explicitly — but you can also just tell VaultBot in chat ("don't use
Wikipedia", "keep your answers short") and it will store that as a
directive note itself.

The more you talk to VaultBot, the more it learns about how you work.
That's the design: growth through conversation, not configuration.