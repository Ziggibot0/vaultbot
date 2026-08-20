# AGENTS.md — VaultBot agent workflow

Operational rules for AI agents working in this repo. Human-facing detail
lives in `vaultbot/CONTRIBUTING.md`; this file is the concise checklist an
agent must follow. If this file and CONTRIBUTING.md disagree, CONTRIBUTING.md
wins — flag the discrepancy instead of guessing.

## Repo layout

- Repo root is `C:\Users\skell\Desktop\Vault2` (NOT `vaultbot/` — that's a
  subfolder). Confirm with `git rev-parse --show-toplevel`.
- Backend source: `vaultbot/vaultbot_backend/`
- Procedures: `vaultbot/System/Procedures/`
- CI workflow: `.github/workflows/ci.yml`

## Hard rules (non-negotiable)

1. **Never commit personal data, vault contents, or secrets.** `.gitignore`
   covers most; verify with `git status` before every push. If a new secret
   file appears, add it to `.gitignore` in the same PR.
2. **No silent fallbacks.** Every `except Exception` must raise, surface via
   `notify_problem`, narrow to a specific exception, or carry
   `# noqa: BLE001 — <reason>`. `tests/test_no_silent_swallow.py` AST-scans
   for violations and CI fails on them.
3. **One concern per branch.** Don't mix unrelated changes in one PR. If the
   working tree has unrelated edits, stash or move them aside first.
4. **CI is the gate.** `ruff check --select F` and `pytest -m unit` are hard
   gates. Run both locally before pushing; don't push and hope.

## Workflow (branch → commit → PR → CI → merge)

1. `git checkout main && git pull --ff-only origin main`
2. `git checkout -b <type>/<scope>-<short-desc>` (e.g. `fix/research-sources`)
3. Stage ONLY the files for this change. `git status` to confirm nothing
   unrelated is staged.
4. Commit with a conventional message: `type(scope): description`.
5. `git push -u origin <branch>`
6. `gh pr create --base main --head <branch> --title ... --body ...`
   Fill the PR template's safety checklist (`.github/pull_request_template.md`).
7. `gh pr checks <N>` — wait for both Python 3.11 and 3.12 to go green.
8. `gh pr merge <N> --squash --delete-branch`

Branch protection on `main` requires a code-owner approval (`@Ziggibot0`)
before any merge. Do NOT use `--admin` to force-merge past review — that
bypasses the sign-off gate. The vaultbot's `review_contributions` tool
enforces the same rule: it refuses to merge until the PR has an APPROVED
review, and reports "awaiting approval" otherwise.

## Gotchas

- `gh` is at `%LOCALAPPDATA%\Programs\gh\bin\gh.exe`, authenticated as
  `Ziggibot0`, on the user PATH. Use `gh pr merge`, NOT local squash+push.
- `.ps1` files (e.g. `setup.ps1`) need a UTF-8 BOM. Editing tools strip it;
  restore with
  `[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($true))`.
  Without the BOM, PowerShell mis-parses Unicode (em-dashes, box-drawing).
- Remote push "errors" ("Cannot create ref", "protected ref") are warnings;
  the `ref -> ref` line confirms success. Exit code 1 is normal.
- Untracked files block `git checkout`; move them to `$env:TEMP` first.

## Before declaring done

- `git status` is clean (or only the intended files are staged).
- Backend boots: `python vaultbot/vaultbot_backend/main.py` with no
  `ImportError`/`Traceback`.
- If the fix is a recurring class of problem, add a procedure under
  `vaultbot/System/Procedures/` (per `.github/instructions/PROCEDURE-DIRECTIVE.instructions.md`).
