# Changelog

All notable changes to VaultBot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note on pre-1.0 versioning.** VaultBot is pre-1.0. Until `1.0.0`, the
> public API is the *vault* (notes, procedures, directives) and the
> *installer*, not the Python package. Breaking changes to those surfaces
> bump the minor version; everything else is a patch. The `0.x` series is
> the "proof of concept" phase described in the
> [Project Mission](README.md#project-mission) — the architecture is in
> place, the proof is not yet demonstrated.

## [Unreleased]

## [0.3.0] - 2026-08-22

This release adds trust-reranked retrieval, per-vault identity, security
hardening on all mutating endpoints, and a more self-sufficient installer
that can provision Python, Git, Ollama, and Obsidian automatically. It is a
**minor** bump because the layout rename (`vault/` → `myvault/`) is a
breaking change to the vault/installer contract.

### Added

- **Trust reranking** — `FusedRetriever` now reranks results by source
  trust tier before returning them to the LLM, reducing hallucination from
  low-trust sources (#226).
- **Per-vault identity** — each vault gets its own instance ID, decoupled
  from the GitHub account, so multiple vaults can share one account
  without colliding (#286).
- **Auto-install prerequisites** — the installer now installs Python, Git,
  and Ollama when they're missing, instead of requiring them up front
  (#290). It also auto-installs Obsidian and deep-links into the vault
  (#293).
- **Obsidian sign-in guide** — non-technical users are walked through
  GitHub sign-in inside Obsidian's own browser pane (#291).
- **Settings UI sections** — the Obsidian settings pane now collapses into
  navigable sections instead of one long scroll (#292).
- **Governance docs** — `GOVERNANCE.md` added and linked from the README
  (#308).
- **FUNDING.yml** — GitHub Sponsors link added (#266).
- **Feature-request template** — issue template for feature requests
  (#265).

### Changed

- **Layout rename** — `vault/` is now `myvault/` and the vault folder name
  is no longer hardcoded; this is a breaking change to the installer
  contract (#225, #267).
- **Contribution flow** — the contrib tool now rebases onto upstream main
  before pushing, reducing stale-PR churn (#287).
- **Auth enforcement** — all mutating HTTP endpoints now require
  authentication, not just a subset (#297).
- **SSRF protection** — `/llm/*` mutators now block server-side request
  forgery and require auth (#295).
- **Reasoning disabled on small models** — OpenAI-compat calls to small
  models no longer send `reasoning_effort`, which caused errors on some
  providers (#137, #299).
- **OpenRouter seeded** — OpenRouter is now a default provider out of the
  box, so first-run doesn't silently fall back (#309).
- **Index metadata format** — switched from `pickle` to JSON, eliminating
  a deserialization attack surface (#254, #264).
- **CI: Python 3.13 & 3.14** — test matrix now includes 3.13 and 3.14
  (#283).
- **CI: pyright version drift gate** — CI fails if pyright's version
  drifts, preventing silent type-check regressions (#302).
- **CI: debt ratchet on Windows** — the ratchet script now runs locally on
  Windows without path errors (#303).
- **Pyright debt cleared** — `ws.py`, `subprocess_utils.py`, `services.py`
  are now fully typed (#301).
- **Dev deps deduplicated** — `requirements-dev.txt` removed; dev deps
  live only in `pyproject.toml` (#300).
- **Doc polish** — stray fences, dangling fragments, wrong directives, and
  `.gitignore` mojibake fixed (#263).
- **Contact links** — `config.yml` contact links now route to Discussions
  instead of a dead email (#285).
- **Updater targeting** — the auto-updater now targets the latest release
  tag instead of `main`, so updates are always from a tagged release
  (#284).
- **Installer fallback removed** — the installer aborts cleanly instead of
  falling back to a zip download when git clone isn't available (#278).
- **No GitHub account required** — the installer no longer requires a
  GitHub account to install or update (#282).
- **Branch protection documented** — the "up-to-date before merge" rule is
  now documented in CONTRIBUTING (#294).

### Security

- SSRF blocked on `/llm/*` mutators; auth required (#295).
- All mutating endpoints now require auth (#297).
- Pickle deserialization replaced with JSON for index metadata (#254, #264).

### Dependencies

- actions/checkout 4 → 7 (#311)
- gitleaks-action 2 → 3 (#310)
- action-gh-release 2 → 3 (#312)
- actions/cache 4 → 6 (#313)
- docker 7.1.0 → 7.2.0 (#314)
- edge-tts 7.2.7 → 7.2.8 (#315)
- idna 3.18 → 3.19 (#316)
- lxml 6.1.1 → 6.1.2 (#317)
- uvicorn 0.52.3 → 0.52.4 (#318)
- ruff 0.16.0 → 0.16.3 (#320)

## [0.2.0] - 2026-08-22

This release restructures the repository layout, thins the backend, and
hardens the self-improvement and contribution paths. It is a **minor**
bump because the vault/installer layout changed (a breaking change to the
installer contract, per the pre-1.0 versioning note above).

### Added

- **Thin backend** — the backend is now a thin interpreter; capabilities
  live in procedures, not inline `.py` modules. Enforced by a *thinness
  ratchet* in CI.
- **Debt ratchet** — CI fails if pyright/pytest debt grows past a
  committed baseline, making debt reduction monotonic.
- **Prove-Code-Change gate** — `safe_write` rejects edits that import
  non-VaultBot modules without a `doc_source` (official-docs URL). Backed
  by `doc_domains.py` (extensible domain map) and `code_run_guard.py`
  (read-only `code_run`).
- **Contribution system hardening** — code-owner approval required before
  merge, PRs authored as a dedicated bot account, opt-in cost-safe
  contribution model, and a CI pre-flight gate before submitting PRs.
- **Procedures** — provenance + rationale requirements, `Run-CI-Gates`,
  `Iterate-PR`, and `Triage-GitHub-Issues`.
- **Google OAuth hardening** — state/PKCE and reflected-value escaping in
  the OAuth callback.
- **Documentation** — `CHANGELOG.md`, `docs/adr/` (architecture decision
  records), `ROADMAP.md`, and README badges.

### Changed

- **Repository layout** — `vault/` + `vaultbot-stuff/` + thin backend
  restructure; the vault is now a sibling of the backend, not an ancestor.
- **Retrieval** — simplified to 3 channels with a trigger/inhibitor
  gradient; golden set made reproducible in CI.
- **Installer** — dark-mode default, 4B small model, GitHub-auth walkthrough,
  BOM stripping, and self-heal on stale clones.
- **Dependencies** — replaced `httpx2` with `httpx`; declared the missing
  `edge-tts` TTS provider.

### Security

- `code_run` is read-only by default; file-write primitives raise
  `PermissionError` unless `allow_write=true`.
- TTS/STT provider failures now surface a loud diagnosis instead of
  degrading silently.

## [0.1.0] - 2026-08-19

Initial tagged release. This is the first cut of the "sustainable AI
inference with provenance" proof-of-concept: a retrieval-augmented
research assistant that lives inside an Obsidian vault, runs on a small
local model, and treats the vault — not model weights — as its knowledge
base.

### Added

- **Self-improvement engine** — `code_read` / `code_write` / `code_run` /
  `safe_write` / `tool_create` / `git_rollback`, with multi-stage
  verification (AST → import-graph → pytest → auto-rollback) before any
  backend edit lands.
- **Procedure engine** — compile-then-execute runtime (`procedure_compiler`
  + `step_gate_runtime`), deterministic code steps in subprocesses,
  recursive `run_procedure` with cycle/depth guards, and a grading loop
  (drift feedback + verified-boost retrieval).
- **Fused retrieval** — vector similarity + graph proximity + lexical BM25,
  with a trigger/inhibitor feedback gate.
- **Research engine** — multi-engine web search (DuckDuckGo, Marginalia,
  arXiv), multi-round dig with gap detection, and LLM synthesis into
  sourced notes.
- **Closed-set citation gate** — every knowledge claim must cite a vault
  note; uncited claims are rejected.
- **Autonomous researcher** — background thread that scans for knowledge
  gaps, ranks them by learning value, researches them, and writes notes.
- **Identity** — `IDENTITY.md` (stable self-concept) + `SELF_MODEL.md`
  (regenerated narrative).
- **Community contribution system** — fork-based PRs, opt-in toggle,
  safety review, and a torture-test gate before merge.
- **Google Workspace integration** — OAuth-authenticated Calendar, Tasks,
  and Docs.
- **Talk Mode** — voice conversations with any STT/TTS provider.
- **One-command installer** — `setup.ps1` / `setup.sh` with a
  grandma-proof setup wizard.
- **CI hardening** — ruff (full rule set), pyright hot-path, pytest unit,
  secret scan (gitleaks), dependency audit (pip-audit), and *ratchets*
  (thinness + debt) that make regressions monotonic.

### Security

- Shared-secret auth between plugin and backend.
- Secret-scrubbed subprocess environment for all LLM-authored code.
- Resource limits (memory/CPU/fork caps) on POSIX.
- Append-only JSONL session audit trail with automatic redaction.

---

## Versioning policy

- **`MAJOR`** — a breaking change to the vault schema, the procedure
  format, or the installer contract.
- **`MINOR`** — a new capability (a new tool, procedure family, or
  integration) that is backward-compatible.
- **`PATCH`** — a bug fix or internal refactor with no user-visible
  behavior change.

Releases are cut by the project custodian (see
[CONTRIBUTING.md](CONTRIBUTING.md)). Each release is tagged in git and
published as a GitHub Release with notes linking to the relevant issues.
