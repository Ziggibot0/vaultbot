# Contributing to VaultBot

Thanks for your interest in improving VaultBot. This guide covers the
practical bits: how to develop safely, what to commit, and what to keep
private.

## The golden rule

**Never commit personal data.** VaultBot lives inside someone's vault —
their notes, their chat history, their API keys. The `.gitignore` already
excludes these, but double-check before pushing:

- `.env` — contains API keys and the owner's name
- `vaultbot/` contents — the user's notes, chats, research
- `vaultbot_backend/sessions/` — chat logs
- `vaultbot_backend/identity/` — the user's identity files (IDENTITY.md,
  SELF_MODEL.md, GOALS.md) — these are personal, regenerate per user
- `learningMaterial/` — the user's PDFs

The `baseline/` folder holds templates; the `vaultbot_backend/identity/`
folder holds *one user's* live identity. Don't confuse them.

## Development setup

```bash
git clone <your-fork>.git
cd vaultbot
python -m venv vaultbot_venv
source vaultbot_venv/bin/activate   # or vaultbot_venv\Scripts\activate on Windows
pip install -r vaultbot_backend/requirements.txt
ollama pull qwen3.6:latest nomic-embed-text
cp .env.example .env   # fill in your values
python vaultbot_backend/main.py
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

## What to commit

- Backend source code (`vaultbot_backend/*.py`)
- The Obsidian plugin (`.obsidian/plugins/vaultbot/`)
- `baseline/` directive templates
- `README.md`, `CONTRIBUTING.md`, `LICENSE`
- `.env.example`, `.gitignore`, `requirements.txt`
- `start_backend.bat`

## What NOT to commit

- `.env` (secrets)
- `vaultbot_venv/` (regenerated per install)
- `vaultbot/` (the user's vault contents)
- `vaultbot_backend/sessions/`, `vaultbot_backend/identity/` (personal data)
- `vaultbot_backend/vaultbot_index/` (regenerated FAISS index)
- `vaultbot_backend/kokoro_models/`, `stt_models/` (large model files —
  document how to download them instead)
- `learningMaterial/` (user's PDFs)

## Coding style

- UTF-8 everywhere. The agent's `code_write` once corrupted every Unicode
  character in a file by writing with the wrong encoding — don't repeat
  that. Always write with `encoding="utf-8"`.
- Keep the LLM-call economy tight. The research engine, textbook weaving,
  card building, and MOC clustering are all LLM-free by design. Don't add
  LLM calls to those paths — they're the reason VaultBot can run on a free
  local model.
- New tools go in `custom_tools/` as self-contained `.py` files with a
  `SCHEMA` dict and a `run(args: dict) -> dict` function. They hot-load.
- New backend modules: add them to `_CORE_FILES` in `self_improver.py` so
  `safe_write` knows to verify the import graph when they're edited.

## Pull requests

1. Fork and branch from `main`.
2. Keep your vault and `.env` out of the commit (`.gitignore` handles this,
   but verify with `git status` before pushing).
3. Test that the backend boots cleanly: `python vaultbot_backend/main.py`
   should start without `ImportError` or `Traceback`.
4. If you added a tool, mention it in the README's feature list.
5. Keep the LLM-call economy — don't introduce gratuitous LLM calls in
   loops that are currently LLM-free.

## Related

- [[Implementation-Plan-Architecture-Modules]] — the architecture modules
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the development plan
- [[Vault-Longevity-Architecture]] — long-term architecture
- [[Exemplar-Tool-Creation]] — how to create tools properly
