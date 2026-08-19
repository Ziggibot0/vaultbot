---
type: procedure
model_cartridge: big
tags:
  - troubleshooting
  - self-updater
  - plugin
  - update
  - silent-failure
covers: "Update from GitHub" button doesn't pick up all changes — procedures, identity rules, architecture docs, setup scripts, or other repo-tracked files not synced after update
status: raw
baseline: true
created: 2026-08-18
summary: Diagnose Self-Update Incomplete (Updater Only Syncs Subset of Repo)
---

# Diagnose Self-Update Incomplete (Updater Only Syncs Subset of Repo)

General class: **any self-updater that downloads a full repo archive but only
copies a subset of it to the live installation, silently leaving the rest
stale.** Symptom: the user clicks "Update from GitHub" and it reports success,
but new procedures, identity rules, architecture docs, setup scripts, or other
repo-tracked files don't appear — the bot keeps running with old knowledge.

## Root cause pattern

The updater in `main.js` `performSelfUpdate()` downloads the full GitHub
tarball (which contains every tracked file) but then only copies *specific
subtrees* to the live vault. Any repo-tracked path that isn't in the copy list
is silently skipped. The user has no way to know — the button says "updated"
and the version number bumps, but the bot's brain (procedures, identity,
architecture) is still running the old version.

## Symptom → likely cause decision tree

1. New procedures / identity rules / architecture docs don't appear after update.
   → The updater only copied `vaultbot/vaultbot_backend/` and the plugin files,
   skipping `vaultbot/System/`, `vaultbot/baseline/`, `vaultbot/docs/`, and
   top-level config files.

2. Setup scripts (`setup.ps1`, `setup.sh`) or `pyproject.toml` not updated.
   → Same root cause — these live at the `vaultbot/` root, not under
   `vaultbot_backend/`, so the old copy list never reached them.

3. `.github/` CI workflows or templates not updated.
   → The updater never synced `.github/` at all.

4. Backend code *did* update but procedures didn't.
   → Confirms the partial-copy diagnosis: backend subtree was in the copy list,
   System/ was not.

## Concrete steps

1. **Read `performSelfUpdate()` in `main.js`.** Find every `copyCodeTree()`
   call and every `fs.copyFileSync()` call. List every source path that gets
   copied. Anything NOT in this list is a silent gap.

2. **Compare against `git ls-files`.** Run `git ls-files` in the repo and
   group by top-level directory. Any directory with tracked files that has
   no corresponding `copyCodeTree` call is a gap.

3. **Check the tar `--exclude` list.** Make sure exclusions only cover
   runtime state (sessions, logs, indexes, pycache, etc.) and never exclude
   actual source/knowledge files. Exclusions are scoped with `*/` prefixes
   and should match archive paths, not live paths.

4. **Verify `copyCodeTree` semantics.** It only overwrites files that exist
   in the archive — it does NOT delete files that exist only in the live
   vault. This means user-created notes, chat logs, and bot-authored
   procedures that aren't in the repo are always preserved. Confirm this
   before broadening the copy scope.

5. **Verify `.gitignore` coverage.** User-content dirs (`vaultbot/Memory/`,
   `vaultbot/Knowledge/`, `User/`) must be in `.gitignore` so they never
   appear in the GitHub tarball. If they're gitignored, `copyCodeTree` will
   never see them in the archive and will never touch them in the live vault.

6. **Broaden the copy scope.** Replace narrow subtree copies
   (`vaultbot/vaultbot_backend/` only) with a full `vaultbot/` tree copy.
   Add `copyCodeTree` calls for `.github/` and root-level meta files
   (`.gitignore`, `.pre-commit-config.yaml`, `README.md`).

7. **Test end-to-end.** After the fix, verify that:
   - A new procedure pushed to the repo appears after "Update from GitHub"
   - User-content dirs (`Memory/`, `Knowledge/`) are untouched
   - `data.json` (plugin settings) is preserved
   - Locally-modified tracked files are backed up to
     `.vaultbot-update-backup/` before being overwritten

## Prevention

- The updater should copy the **entire repo tree** (minus gitignored state),
  not hand-pick subtrees. Any new top-level directory added to the repo in the
  future will then automatically be synced without needing to update the
  copy list.
- The "What gets updated" comment block in `performSelfUpdate()` must list
  every synced path so future maintainers can see the scope at a glance.