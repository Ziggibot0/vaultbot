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
irm https://github.com/Ziggibot0/vaultbot/raw/main/vaultbot/setup.ps1 | iex
```
```bash
# macOS / Linux
curl -fsSL https://github.com/Ziggibot0/vaultbot/raw/main/vaultbot/setup.sh | bash
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

The project has a typed error layer for surfacing:
- `error_types.py` — `Diagnosis` dataclass + `ProblemCategory` enum
- `diagnostics.py` — `classify_error(exc, context)` translates any
  exception into a `Diagnosis`
- `chat_helpers.py` — `notify_problem(svc, websocket, exc, context=,
  user_message=, remedy_hint=)` sends a typed WS event to the user

Use `context={"category": "retrieval_broken"}` (or other subsystem
categories) to tag errors that don't have a distinctive exception signature.

## What to commit

- Backend source code (`vaultbot/vaultbot_backend/*.py`)
- The Obsidian plugin (`.obsidian/plugins/vaultbot/`)
- `vaultbot/baseline/` directive templates
- `README.md`, `CONTRIBUTING.md`, `LICENSE`
- `vaultbot/.env.example`, `.gitignore`, `pyproject.toml`
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
