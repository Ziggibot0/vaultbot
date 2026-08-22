---
description: "Use when creating, approving, or merging a pull request in this repo. Covers the two-account split: author PRs as ziggibot-uni, approve and merge as Ziggibot0."
---

# GitHub Account Split for Pull Requests

This repo uses a two-account split because GitHub forbids approving your own
pull request. `Ziggibot0` owns the repo and is the only account that can
approve + merge. `ziggibot-uni` is a normal (non-admin) account whose only
job is to author PRs so `Ziggibot0` can approve them.

## The rule

When you make a change to this repo and need to open a PR:

1. **Author the PR as `ziggibot-uni`** (never as `Ziggibot0`).
2. **Approve the PR as `Ziggibot0`**.
3. **Merge the PR as `Ziggibot0`**.

Do NOT author the PR as `Ziggibot0` — you cannot approve your own PR, so the
approval gate would deadlock.

## Account roles

| Account | Role |
|---------|------|
| `Ziggibot0` | Repo owner / code owner. The ONLY account that can approve + merge. |
| `ziggibot-uni` | Normal (non-admin) account. Authors PRs only. |

## Commands

```powershell
# Author the PR as ziggibot-uni (use the create_pull_request tool with head = branch name)

# Approve as Ziggibot0
gh auth switch --user Ziggibot0
gh pr review <N> --approve --body "Approving so this can merge."

# Merge as Ziggibot0
gh pr merge <N> --squash --delete-branch
```

Do NOT use `--admin` to force-merge past review — it does not bypass the
review gate and defeats the sign-off requirement.