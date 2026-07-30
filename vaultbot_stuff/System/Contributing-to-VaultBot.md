---
type: system-doc
title: Contributing to VaultBot
tags: [documentation, contributing, community, github]
created: 2025-01-20
---

# How to Contribute to VaultBot

VaultBot has a built-in community contribution system. Any VaultBot
instance can submit pull requests to the upstream repo — with the
user's permission. Here's how it works.

## For Contributors (Other VaultBot Users)

### Prerequisites

1. **GitHub account** — you need a GitHub account
2. **Personal access token** — create a token with `repo` scope at
   https://github.com/settings/tokens
3. **Add token to .env** — add `GITHUB_TOKEN=ghp_your_token_here` to
   your `.env` file

### How to Contribute

When your VaultBot makes an improvement (fixes a bug, adds a tool,
improves documentation), it can submit the change as a PR:

1. **VaultBot checks permission** — it looks for `GITHUB_TOKEN` in
   `.env` or `allowContributions: true` in plugin settings. If
   neither is set, it refuses and explains how to opt in.

2. **Safety scan** — VaultBot runs a safety check on the changes
   before submitting. It refuses to submit if it detects:
   - `.env` or secrets in the changes
   - Files outside allowed paths (`vaultbot_stuff/` and
     `.obsidian/plugins/vaultbot/`)
   - Dangerous code patterns (eval, exec, os.system, etc.)
   - Changes to `.gitignore` that un-ignore sensitive paths

3. **Fork-based submission** — if you don't have write access to the
   upstream repo (most users), VaultBot:
   - Forks `ziggibot-uni/vaultbot` to your GitHub account
   - Creates a contribution branch
   - Commits and pushes to your fork
   - Creates a cross-fork PR back to `ziggibot-uni/vaultbot`

4. **PR created** — the PR appears on GitHub with a description of the
   changes. Sean's VaultBot reviews it (see below).

### What Happens After Submission

Sean's VaultBot reviews every PR through a two-layer process:

1. **Safety scan** — checks for secrets, dangerous code, path
   violations, .gitignore tampering
2. **Torture test** — runs syntax checks, import checks, malware
   scans on the PR's changed files

If both pass, Sean approves the merge. If any check fails, the PR is
rejected with a comment explaining what failed.

### What You Can Contribute

- Bug fixes in backend Python code (`vaultbot_stuff/vaultbot_backend/`)
- Plugin improvements (`.obsidian/plugins/vaultbot/`)
- New custom tools (`vaultbot_stuff/vaultbot_backend/custom_tools/`)
- Documentation improvements
- Setup script fixes
- Baseline template improvements

### What You Cannot Contribute

- Changes to `.env` or any secrets file
- Changes outside allowed paths (e.g., to `User/`, `Memory/`,
  `Knowledge/`, `sessions/`, `identity/`)
- Changes to `.gitignore` that un-ignore sensitive paths
- Code with dangerous patterns (eval, exec, os.system, pickle.loads,
  raw sockets, non-GitHub network calls)
- Binary files or files larger than 5000 lines

## For Sean (Repo Owner / Reviewer)

### Reviewing PRs

Tell your VaultBot "review PRs" or "check contributions." It will:

1. List all open PRs on `ziggibot-uni/vaultbot`
2. Run the safety scanner on each PR
3. Run the torture test on PRs that pass safety
4. Report the results to you
5. Ask for your approval to merge

See [[Review-PR-Procedure]] for the full deterministic process.

### Merging PRs

VaultBot will not merge without your explicit approval. When you say
"merge PR #X", it calls `review_contributions` with `merge=True` and
the PR number. The merge only happens if the PR passed all checks.

### Torture Test Details

The torture test checks:

| Test | What it checks |
|------|---------------|
| Python syntax | `py_compile` on all changed .py files |
| JS syntax | `node --check` on changed .js files |
| .gitignore tampering | No sensitive paths un-ignored |
| Malware scan | No reverse shells, deserialization, .env reads |
| Path whitelist | Only allowed paths modified |

## Privacy & Safety

- Your `GITHUB_TOKEN` stays in `.env` — it's gitignored, never
  committed, never logged, never put in a PR body
- Your chat history, notes, and personal data are never included in
  contributions — the safety scan blocks them
- Sean's VaultBot independently verifies every PR — trust is not
  required, only code that passes all checks gets merged
- The token is only used for: forking the repo, pushing to the fork,
  and creating the PR — nothing else