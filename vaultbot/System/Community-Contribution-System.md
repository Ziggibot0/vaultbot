---
type: system-design
title: Community Contribution System
tags:
  - system
  - architecture
  - community
  - github
  - contributions
created: 2025-01-20
status: raw
baseline: true
summary: "VaultBot creates cross-fork PRs to verify user-generated AI tokens before merging them into the main branch, preventing unauthorized token contributions without approval review."
---

# Community Contribution System

## Goal

Allow any VaultBot instance (with user permission) to submit pull requests
to the upstream VaultBot repo. The maintainer's VaultBot reviews each PR for safety
and runs a torture test before merging to main. This way, other users' AI
tokens pay for the thinking and contributions — not the maintainer's.

## Architecture

```
┌──────────────────────────┐     ┌──────────────────────────┐
│  Contributor's VaultBot   │     │  Maintainer's VaultBot    │
│  (any user, opt-in)       │     │  (repo owner / reviewer)  │
├──────────────────────────┤     ├──────────────────────────┤
│  1. User gives permission │     │  1. List open PRs         │
│  2. Fork upstream repo    │     │  2. Review each PR:       │
│  3. Create branch         │     │     - Safety scan         │
│  4. Commit changes        │     │     - Diff analysis       │
│  5. Push to fork          │     │  3. Torture test:         │
│  6. Create cross-fork PR  │     │     - Import check        │
│         │                │     │     - Syntax check        │
│         ▼                │     │     - Functional test     │
│  ┌─────────────────┐     │     │  4. Merge or reject       │
│  │  GitHub          │     │     │     - Comment with result │
│  │  ziggibot-uni/   │◄────┼─────┼──►  PR review + merge     │
│  │  vaultbot        │     │     │                          │
│  └─────────────────┘     │     └──────────────────────────┘
└──────────────────────────┘
```

## Contributor Flow (submit_contribution tool)

### Permission Gate

The user must explicitly opt in. Two mechanisms:

1. **GITHUB_TOKEN present** — having a token in `.env` implies consent
2. **Plugin setting** — `allow_contributions: true` in `data.json`
   (future: add a toggle in plugin settings UI)

The tool checks both. If neither is set, it refuses and explains how to
opt in.

### Fork-Based PR Flow

When the user does NOT have write access to `ziggibot-uni/vaultbot`:

1. **Fork the repo** — `POST /repos/ziggibot-uni/vaultbot/forks` using
   the user's GITHUB_TOKEN. If fork already exists, GitHub returns 200
   instead of 202 — handle both.

2. **Add fork as a remote** — `git remote add fork https://github.com/
   {username}/vaultbot.git` (or update URL if remote exists)

3. **Create a branch** — `git checkout -b contribution-{timestamp}`

4. **Stage and commit changes** — same as current flow, but run the
   Safe-Commit-Push-Procedure's safety checks first (no .env, no
   personal data, no sensitive files)

5. **Push to fork** — `git push -u fork {branch}`

6. **Create cross-fork PR** — `POST /repos/ziggibot-uni/vaultbot/pulls`
   with `head: "{username}:{branch}"` and `base: "main"`. This tells
   GitHub to compare the fork's branch against the upstream's main.

7. **Switch back to main** — clean up local state

### Write-Access Flow (Maintainer)

When the user HAS write access (maintainer's own VaultBot), the current flow
works: push directly to origin and create a PR. The tool detects this by
checking if `git push origin {branch}` succeeds.

## Reviewer Flow (Maintainer)

### review_contributions tool

Lists all open PRs on `ziggibot-uni/vaultbot` and for each PR:

1. **Fetch PR metadata** — title, body, author, branch, files changed
2. **Fetch the diff** — `GET /repos/{owner}/{repo}/pulls/{number}/files`
3. **Safety scan** — check each changed file for:
   - `.env` or secrets (tokens, API keys, passwords)
   - Changes outside allowed paths (`vaultbot/` and
     `.obsidian/plugins/vaultbot/`)
   - Dangerous code patterns (`eval(`, `exec(`, `subprocess.call` with
     user input, `os.system(`, `__import__`)
   - Changes to `.gitignore` that un-ignore sensitive paths
   - Hardcoded personal paths (e.g. `C:\Users\`)
   - Binary files or large files
4. **Report** — structured summary of each PR with pass/fail per check

### torture_test tool

Takes a PR number, fetches the branch, and runs:

1. **Syntax check** — `python -m py_compile` on every `.py` file in the
   PR diff
2. **Import check** — `python -c "import main"` in the backend dir with
   the PR's changes applied (checkout the PR branch in a temp clone)
3. **Safe-write check** — verify the changes don't break the safe_write
   import verification (run `safe_write`'s import check on each changed
   `.py` file)
4. **JS syntax check** — `node --check main.js` if plugin files changed
5. **Gitignore check** — verify the PR doesn't modify `.gitignore` to
   un-ignore sensitive paths
6. **No-malware check** — scan for reverse shells, data exfiltration
   patterns, network calls to non-GitHub hosts, file reads of `.env` or
   `sessions/`

### Merge Decision

If all torture tests pass AND safety scan passes:
- Merge via `PUT /repos/{owner}/{repo}/pulls/{number}/merge`
- Comment on the PR with the test results

If any check fails:
- Comment on the PR with the specific failures
- Do NOT merge

## Permission & Safety Guarantees

1. **Token never leaves .env** — the GITHUB_TOKEN is read from
   environment, used in API headers, never written to disk or logged
2. **User opt-in required** — tool refuses without GITHUB_TOKEN or
   `allow_contributions` setting
3. **Safety scan before push** — the contributor's VaultBot runs the
   same safety checks as the Safe-Commit-Push-Procedure before pushing
4. **Reviewer torture test** — the maintainer's VaultBot independently verifies
   every PR before merging
5. **No direct push to main** — all contributions go through PR review
6. **Token scope** — contributors only need `repo` scope (for forking
   and pushing to their fork)

## Baseline Membrane

VaultBot is a single repo that is both the public baseline AND each user's
personal vault. The `.gitignore` keeps personal data out of git, but
procedures and System notes (`vaultbot/System/`) are a gray zone:
some are general-purpose, some are bespoke to one user's vault.

The **baseline membrane** solves this with a single frontmatter field:

```yaml
baseline: true   # ← this procedure ships to everyone
```

**How it works:**

1. **Pre-commit hook** — blocks commits of `System/` `.md` files that
   lack `baseline: true`. Runs locally on every commit.
2. **`submit_contribution` tool** — filters changed files to baseline-only
   before staging. Non-baseline files are excluded with a warning.
3. **`review_contributions` tool** — flags new `System/` files without
   the marker as a high-severity issue during PR review.

**What this means for contributors:**

- Your personal procedures (no `baseline: true`) never appear in PRs.
- Backend `.py` code always ships — no marker needed.
- To share a procedure, add `baseline: true` to its frontmatter.
- The membrane is automatic — you don't need to think about it unless
  you're intentionally sharing a procedure.

**What this means for the maintainer:**

- Every PR is clean by construction — no personal content leaks through.
- The review tool catches missing markers as a safety net.
- The baseline grows organically from the community's best procedures.

## Tools to Build

1. **submit_contribution** (update) — add fork-based flow
2. **review_contributions** (new) — list and review PRs
3. **torture_test** (new) — run safety + import + syntax checks on PRs
4. **Review-PR-Procedure** (new) — deterministic procedure note for
   for the maintainer's VaultBot to follow when reviewing

## Upstream Repo

- Owner: `ziggibot-uni`
- Repo: `vaultbot`
- Main branch: `main`
- URL: https://github.com/ziggibot-uni/vaultbot