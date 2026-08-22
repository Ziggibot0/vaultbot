---
type: claim
status: raw
created: 2026-08-03
summary: "# Contributing to VaultBot"
tags:
  - vaultbot
  - sessions.md
  - knowledge.md
  - pdf.md
---

# Contributing to VaultBot

Thanks for your interest in improving VaultBot. This guide covers the
practical bits: how to develop safely, what to commit, and what to keep
private.

## Project founder & custodian

**Sean Kellogg** is the project founder and custodian, and the sole merge
authority for this repository. He has final say on what ships and on
project direction. He does **not** claim ownership of your contributions —
see the licensing section below. The best way to reach the project is
through [GitHub Issues](https://github.com/Ziggibot0/vaultbot/issues) or
[GitHub Discussions](https://github.com/Ziggibot0/vaultbot/discussions).

## The golden rule

**Never commit personal data.** VaultBot lives inside someone's vault —
their notes, their chat history, their API keys. The `.gitignore` already
excludes these, but double-check before pushing:

- `.env` — contains API keys and the owner's name
- `vaultbot/Memory/` and `vaultbot/Knowledge/` — the user's notes, chats, research
- `vaultbot/vaultbot_backend/sessions/` — chat logs
- `vaultbot/vaultbot_backend/identity/` — the user's identity files (IDENTITY.md,
  SELF_MODEL.md) — these are personal, regenerate per user
- `vaultbot/learningMaterial/` — the user's PDFs

The `baseline/` folder holds templates; the `vaultbot_backend/identity/`
folder holds *one user's* live identity. Don't confuse them.

## Development setup

The fastest way to get a dev environment running is the one-liner installer:

```powershell
# Windows
irm https://github.com/Ziggibot0/vaultbot/raw/main/setup.ps1 | iex
```
```bash
# macOS / Linux
curl -fsSL https://github.com/Ziggibot0/vaultbot/raw/main/setup.sh | bash
```

This creates a `VaultBot/` folder with a fully set-up venv, deps, models,
and `.env`. For development, you'll typically want to clone your fork
instead and run the installer inside it (or set up the venv manually):

```bash
git clone <your-fork>.git
cd vaultbot
python -m venv vaultbot_venv
# No activation needed — invoke the venv's python directly:
vaultbot_venv/Scripts/python.exe -m pip install -r vaultbot/vaultbot_backend/requirements.txt   # Windows
# or: vaultbot_venv/bin/python -m pip install -r vaultbot/vaultbot_backend/requirements.txt       # macOS/Linux
ollama pull qwen3.6:latest nomic-embed-text
cp vaultbot/.env.example .env   # fill in your values
```

The backend is started automatically by the Obsidian plugin. For manual
testing: `vaultbot_venv/Scripts/python.exe vaultbot/vaultbot_backend/main.py`
(Windows) or `vaultbot_venv/bin/python vaultbot/vaultbot_backend/main.py` (macOS/Linux).
```

## Safe self-editing

VaultBot can edit its own source code. If you're adding to the backend,
follow the same safety protocol the agent uses:

1. **Run `preflight_safety_check`** (or just confirm git is clean) before
   editing — so you can roll back.
2. **Test with `code_run` first** — run your new code in the sandbox
   subprocess before writing it to disk.
3. **Use `safe_write` for backend `.py` files** — it verifies the edit
   won't break the import graph (imports `main.py` in a subprocess with
   the new file) and auto-rolls-back if it would. Never use a raw
   overwrite on core modules.
4. **If something breaks, `git_rollback`** restores from HEAD.

## No silent fallbacks

VaultBot follows a fail-loud principle: **all code must fail loudly. No
fallbacks, no silent degradation.** Checking multiple sources is fine,
but trying different mechanisms in a row is lazy — there will always be
more edge cases. If it breaks, it breaks visibly.

When you write an `except` block, choose one of four approaches:

1. **Raise** — remove the try/except entirely. The caller already has
   exception handling and can surface the error.
2. **Surface** — call `notify_problem(svc, websocket, e, context=...)`
   so the user gets a `Diagnosis` card explaining what failed. The chat
   continues, but the user knows.
3. **Narrow** — catch only the specific exception type (e.g.
   `FileNotFoundError`, `PermissionError`) instead of broad `Exception`.
   Let unexpected exceptions propagate.
4. **Keep** — if the except is genuinely best-effort (heartbeat, shutdown
   cleanup, logging-of-logging), add `# noqa: BLE001 — <reason>` so the
   linter stops flagging it and the justification is documented.

**Never** write `except Exception: pass` or `except Exception: return []`
without one of the above. The ruff config selects `BLE001` (blind-except)
and the test `tests/test_no_silent_swallow.py` AST-scans for silent-swallow
patterns. Both will catch new violations.

### Where BLE001 is enforced vs. ignored

The fail-loud rule is **strictly enforced on the hot path** — the three
modules where narrowing actually surfaces real bugs:

| Module | Role |
|--------|------|
| `vault_indexer.py` | Vault indexing / reindexing — the core ingest pipeline |
| `chat_handler.py` | The chat loop — user-facing request handling |
| `fused_retrieval.py` | Multi-channel retrieval — the answer pipeline |

A new broad-except in any of these files fails the `ruff check --select F`
CI gate. No per-file-ignores, no blanket exemptions.

**Background and cleanup modules** (research cycles, post-ingest weaving,
sandbox execution, embedding drift, etc.) are **best-effort by design**.
These modules run asynchronously and must never crash the chat loop over
a transient failure. `BLE001` is blanket-ignored for ~35 such modules via
`[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`. This is deliberate:
annotating each of ~300 individual sites would bloat the diff without
changing behavior, and a background-module crash is less harmful than a
dead chat loop.

When working in a **hot-path file**, every `except Exception` must use one
of the four approaches above — there is no escape hatch. When working in a
**background module**, the blanket ignore means you won't see a ruff
error, but you should still follow the four-approach rule: surface errors
where practical, and only fall back to bare `except Exception` when
genuine graceful degradation is the intended behavior.

The project has a typed error layer for surfacing:
- `diagnostics.py` — `classify_error(exc, context)` translates any
  exception into a `Diagnosis`
- `chat_helpers.py` — `notify_problem(svc, websocket, exc, context=,
  user_message=, remedy_hint=)` sends a typed WS event to the user

Use `context={"category": "retrieval_broken"}` (or other subsystem
categories) to tag errors that don't have a distinctive exception signature.

## CI ratchets (behavior-capture and thinness)

VaultBot's CI enforces two ratchets that prevent quality regressions
monotonically. Both live in `.ci-baseline.json` and are checked by
`.github/workflows/ci.yml`:

### Debt ratchet (issue #21)

The **debt ratchet** (`check_debt_ratchet.py`) wraps the two soft gates
(pyright full, pytest integration) that run with `continue-on-error` so they
surface pre-existing debt without blocking CI. The ratchet re-runs each soft
gate, counts the current violations, and **fails CI if the count exceeds
the committed baseline** in `.ci-baseline.json`. New debt is blocked; the
baseline stays green and is lowered incrementally as debt is paid down.

To lower the baseline: fix some violations, then lower the count in
`.ci-baseline.json` in the same PR.

The **thinness ratchet** (`check_thinness.py`) measures inline backend
Python logic (non-blank, non-comment SLOC in `vaultbot_backend/` excluding
`custom_tools/`, `tests/`, `scripts/`) and **fails CI if it exceeds the
committed baseline** in `.ci-baseline.json`. This enforces the "thin
backend" goal: logic should migrate out of inline `.py` modules into
procedures, `custom_tools/`, or a thin interpreter, and this ratchet makes
that migration monotonic — the count can only go down (or stay flat) as we
thin.

To lower the baseline: move inline logic into a procedure or custom tool,
then lower the `thinness.sloc` value in `.ci-baseline.json` in the same PR.
To accept new inline logic (discouraged), raise the baseline in the same PR.

## What to commit

- Backend source code (`vaultbot/vaultbot_backend/*.py`)
- The Obsidian plugin (`.obsidian/plugins/vaultbot/`)
- `vaultbot/baseline/` directive templates
- `README.md`, `CONTRIBUTING.md`, `LICENSE`
- `providers.example.json` (documents the provider/model registry schema)
- `vaultbot/.env.example`, `.gitignore`, `pyproject.toml`
- `.ci-baseline.json` (debt + thinness ratchet baselines)
- `vaultbot/setup.ps1`, `vaultbot/setup.sh` (one-click installers)

## What NOT to commit

- `.env` (secrets)
- `vaultbot_venv/` (regenerated per install)
- `vaultbot/Memory/`, `vaultbot/Knowledge/` (the user's notes and research)
- `vaultbot/vaultbot_backend/sessions/`, `vaultbot/vaultbot_backend/identity/` (personal data)
- `vaultbot/vaultbot_backend/vaultbot_index/` (regenerated FAISS index)
- `vaultbot/learningMaterial/` (user's PDFs)

## Baseline vs. emergent content

VaultBot ships two kinds of knowledge, and they must not mix:

- **Baseline** (`baseline: true` in frontmatter) — curated, reviewed,
  universal. The *one canonical way* to solve a common problem. Ships to
  every user. Two installs must never have different versions.
- **Emergent** (no `baseline: true`) — what *your* instance learned from
  *your* usage: session logs, tool-call patterns, journal themes, user
  reactions. Stays local. Divergence is expected and fine.

The Dream-Pass is an **emergent** process. It must never write
`baseline: true`, and it must never mutate a `baseline: true` file's
frontmatter. Auto-generated procedures (`Dream-Pattern-To-Procedure`) and
retrieval-tuning metadata (`trigger`/`inhibitor`, learned `when_to_use`)
are personal by default. Promoting something to baseline is a deliberate,
reviewed act — not a side effect of a dream pass.

If you find a `baseline: true` file that was auto-generated or carries
per-user metadata, that's a bug: strip the marker and route the content
through the contribution review flow instead.

## Coding style

- UTF-8 everywhere. The agent's `code_write` once corrupted every Unicode
  character in a file by writing with the wrong encoding — don't repeat
  that. Always write with `encoding="utf-8"`.
- Keep the LLM-call economy tight. The research engine, textbook weaving,
  card building, and MOC clustering are all LLM-free by design. Don't add
  LLM calls to those paths — they're the reason VaultBot can run on a free
  local model.
- New tools go in `vaultbot/vaultbot_backend/custom_tools/` as self-contained `.py` files with a
  `SCHEMA` dict and a `run(args: dict) -> dict` function. They hot-load.
- New backend modules: add them to `_CORE_FILES` in `vaultbot/vaultbot_backend/self_improver.py` so
  `safe_write` knows to verify the import graph when they're edited.

## Pull requests

1. Fork and branch from `main`.
2. Keep your vault and `.env` out of the commit (`.gitignore` handles this,
   but verify with `git status` before pushing).
3. Test that the backend boots cleanly: `python vaultbot/vaultbot_backend/main.py`
   should start without `ImportError` or `Traceback`.
4. If you added a tool, mention it in the README's feature list.
5. Keep the LLM-call economy — don't introduce gratuitous LLM calls in
   loops that are currently LLM-free.



## Releases

VaultBot follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and keeps a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
[`CHANGELOG.md`](CHANGELOG.md). Releases are cut by the project custodian.

### When to cut a release

- **Patch** (`0.1.x`) — a bug fix or internal refactor with no
  user-visible behavior change. Cut whenever a meaningful fix lands.
- **Minor** (`0.x.0`) — a new capability (a new tool, procedure family, or
  integration) that is backward-compatible.
- **Major** (`x.0.0`) — a breaking change to the vault schema, the
  procedure format, or the installer contract. Pre-1.0, breaking changes
  bump the *minor* version (see the note in `CHANGELOG.md`).

### Release checklist

1. Update the `[Unreleased]` section of `CHANGELOG.md` into a dated
   version section, and bump `version` in `pyproject.toml`.
2. Tag the release: `git tag -a v0.1.1 -m "v0.1.1"` and push the tag.
3. Publish a GitHub Release from the tag, with notes linking to the
   issues closed since the last release.

## Branch protection

`main` is protected: it requires a code-owner approval before merge, and
**force-push and branch deletion are disabled** on all branches. This is
deliberate — it preserves the review gate and prevents history rewriting.

Two practical consequences for contributors:

- **Do not rebase-and-force-push a PR branch.** If your branch has
  diverged from `main`, merge `main` into it (or open a fresh branch)
  instead of force-pushing.
- **Orphaned branches cannot be deleted by the bot.** If a PR is closed
  without merging, its branch stays on the remote until a maintainer
  deletes it from the GitHub UI.

## What happens after you submit

Every PR goes through a two-layer review:

1. **Safety scan** — checks for secrets, dangerous code, path violations,
   and `.gitignore` tampering.
2. **Torture test** — runs syntax checks, malware scan, and path whitelist
   verification on the changed files.

If both pass, the maintainer reviews and merges. If any check fails, the PR
is rejected with a comment explaining what failed.

### What you can contribute

- Bug fixes in backend Python code (`vaultbot/vaultbot_backend/`)
- Plugin improvements (`.obsidian/plugins/vaultbot/`)
- New custom tools (`vaultbot/vaultbot_backend/custom_tools/`)
- Documentation improvements
- Setup script fixes
- Baseline template improvements

### What you cannot contribute

- Changes to `.env` or any secrets file
- Files outside `vaultbot/` or `.obsidian/plugins/vaultbot/`
- Changes to `.gitignore` that un-ignore sensitive paths
- Code with dangerous patterns (`eval`, `exec`, `os.system`, `pickle.loads`,
  raw sockets, non-GitHub network calls)

## Licensing & ownership

VaultBot is MIT-licensed. By submitting a pull request, issue, or diff
(including diffs generated by your VaultBot), you confirm that:

- You have the rights to contribute this work under the MIT license.
- Your contribution is licensed to the project and its users under MIT.
- **You retain your copyright** — no copyright assignment to the
  maintainer is required or requested. The maintainer only receives a
  license to use, merge, and redistribute your contribution under MIT,
  the same terms everyone else enjoys.

This follows the standard GitHub inbound=outbound convention. The
maintainer does not claim to own your work; he only curates what ships.

## Contributing via VaultBot (automated reports & diffs)

VaultBot is designed to improve as a shared public good: when your
VaultBot discovers a bug or has an idea for a fix, it can file an issue or
open a draft pull request on this repository so the whole community
benefits. In exchange, everyone gets to run a tool that gets better over
time without any single person paying for all the reasoning.

To keep this safe for everyone, every automated contribution **must**:

1. **Contain no personal or vault data.** Bug reports and diffs must not
   include notes, chat logs, identity files, session history, or `.env`
   contents. VaultBot must strip or summarize any vault content before it
   leaves the machine.
2. **Be filed as a draft** (draft issue / draft PR). The maintainer reviews
   every contribution manually; nothing auto-merges.
3. **Carry the licensing confirmation above.** The report or PR body
   should state that the contribution is offered under MIT.

The maintainer will reject — without merging — any contribution that
contains personal data, vault contents, or anything that looks like a
leaked secret. If you realize a report you filed contains private
material, open a [GitHub Issue](https://github.com/Ziggibot0/vaultbot/issues) and the maintainer will take it down.

## Further reading

The `vaultbot/System/` directory contains architecture docs,
procedures, and design notes that explain how VaultBot works internally.
These are living documents in the vault, not static docs — but they're
useful if you want to understand the design philosophy.

### How to use the `submit_contribution` tool

If your VaultBot has made changes to the framework code that you think would
benefit other users, you can ask it to submit a pull request directly:

1. **Set up a GitHub token.** Create a personal access token with `repo`
   scope at https://github.com/settings/tokens and add it to your `.env`:
   ```
   GITHUB_TOKEN=ghp_your_token_here
   GITHUB_USERNAME=your_github_username
   ```

2. **Ask your VaultBot to submit.** Tell it something like:
   > "Submit your changes to the GitHub repo as a PR"

3. **What happens.** VaultBot will:
   - Stage the uncommitted changes (or specific files you name)
   - Create a contribution branch
   - Commit and push to origin
   - Open a pull request targeting `main`
   - Switch back to your `main` branch

4. **Review.** Sean (the maintainer) reviews every PR manually. Nothing
   auto-merges. If your change benefits the community, it ships.

The tool will refuse to run without a `GITHUB_TOKEN` and will never include
vault contents, chat logs, or personal data — only the code files you've
changed.
