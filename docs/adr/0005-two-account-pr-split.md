# 0005. Two-account PR author/approve split

Status: Accepted

## Context

GitHub forbids approving your own pull request. A single maintainer who
both authors and merges PRs therefore cannot use the review gate at all —
they either self-merge (bypassing review) or deadlock waiting for an
approval that can never come.

VaultBot is maintained by a single custodian, but it *authors* PRs through
an automated agent. That agent must not be able to approve its own work.

## Decision

Use **two GitHub accounts** with a strict role split:

- **`ziggibot-uni`** — a normal (non-admin) account whose only role is to
  *author* PRs.
- **`Ziggibot0`** — the code owner, the only account that can *approve*
  and *merge*.

The flow is: author as `ziggibot-uni` → approve as `Ziggibot0` → merge as
`Ziggibot0`. No `--admin` flag (it does not bypass the review gate).

## Consequences

- **Easier:** the review gate is real even for a solo maintainer. Every
  merge has a genuine approval from a distinct identity.
- **Harder:** the workflow is two-step and account-sensitive. Authoring a
  PR with the wrong account deadlocks the approval flow, and the
  `VAULTBOT_GH_BOT_USER` env var must be set so the bot retrieves the
  right token.
- **Given up:** the convenience of a single account doing everything. The
  split is a deliberate friction that preserves the integrity of the
  review gate.
