# Security Policy

## Reporting a vulnerability

If you find a security vulnerability in VaultBot, please **do not** open a
public GitHub issue. Report it privately instead:

- Email: **skelogg124@gmail.com**
- Subject line: `[VaultBot security] <short summary>`

Please include:

- A description of the issue and its impact.
- Steps to reproduce (a minimal repro is ideal).
- Any affected versions or commits.

## What to expect

- Acknowledgement of your report within 7 days, usually sooner.
- An assessment and a fix timeline based on severity.
- Coordinated disclosure: we'll publish a fixed release and credit you
  (if you'd like) once the issue is resolved.

## Scope

This policy covers the VaultBot backend (`vaultbot_backend/`), the
Obsidian plugin (`.obsidian/plugins/vaultbot/`), and the standard
`baseline/` directive templates shipped with this repository.

It does **not** cover:

- Bugs in dependencies — report those upstream.
- Issues caused by user modifications to their own vault or identity files.
- Exposure of private data that results from a user committing their
  `.env` or vault contents against the guidance in `CONTRIBUTING.md`
  (that's a user-side misconfiguration, not a VaultBot vulnerability).

## Safe use

VaultBot runs locally and only touches your machine, your Ollama
instance, and the research backend you configure. It does not phone home.
Bug reports and draft PRs filed by VaultBot are opt-in and must follow the
privacy guardrails in `CONTRIBUTING.md` (no vault contents, no secrets).