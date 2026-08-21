---
type: claim
status: raw
created: 2026-08-16
summary: Repository Hygiene Rules
tags:
  - claim
  - docs
---

# Repository Hygiene Rules

These rules keep the codebase clean as it evolves. The repo was restructured on 2026-08-16 — see the commit history for what changed.

## Never commit these

- **Runtime state**: `*_log.json`, `embedding_drift.json`, `curriculum_state.json`, `touch_counts.json`, `chat_loop_checkpoint.json`, `qa_queue.json`, `calibration_log.json`, `claim_verification_log.json`, `consolidation_log.json`, `conversation_state.json`, `working_memory_state.json`
- **Logs**: `*.log`, `backend_*.txt`, `backend*.err*`, `debug_chat.log`, `maintenance.log`, `boot.log`, `pythonw_*.log`
- **Test artifacts**: `_pytest*.txt`, `test_err.log`, `test_out.log`, `test_install*.log`, `test_update*.log`
- **Scratch files**: `temp_*`, `*_preview.txt`, `vault_reset_batch_*.txt`, `*_staging.json`
- **Scan results**: `stale_*.json`, `proc_audit*`, `stale_refs_report.json`, `*_scan.json`
- **Backups**: `*.bak`, `*.bak.*`, `*.tmp`, `*.patched`, `*.pre_*`
- **Session data**: `sessions/`, `checkpoints/`, `session_state/`, `partials/`

## Where things belong

- **Source code**: `vaultbot_backend/` (not root)
- **Tests**: `vaultbot_backend/tests/`
- **Installers**: `setup.ps1` / `setup.sh` (not root)
- **CI**: `.github/workflows/`
- **Vault knowledge**: Obsidian manages these — the vault root outside `vaultbot/`
- **Runtime state**: created by the backend at runtime, never committed

## Pre-commit checklist

- `ruff check vaultbot_backend/` — zero errors
- `ruff format --check vaultbot_backend/` — clean
- No files matching the "Never commit" list above
- Backend imports cleanly: `python -c "import main"` from `vaultbot_backend/`
