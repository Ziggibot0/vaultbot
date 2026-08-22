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

### Added

- `doc_domains.py` — extensible doc-domain map for the Prove-Code-Change
  gate (stdlib auto-detection + PyPI metadata fallback).
- `code_run_guard.py` — read-only guard for `code_run`, closing the
  file-write bypass path so `safe_write` is the only way to modify backend
  source.

### Changed

- `Check-API-Against-Docs` now imports `resolve_doc_domain` instead of a
  hardcoded map.

### Security

- `code_run` is read-only by default; file-write primitives raise
  `PermissionError` unless `allow_write=true`.

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
