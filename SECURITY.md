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

## Agent code execution — threat model

VaultBot is a **self-modifying agent**: the LLM that drives the chat loop can
invoke tools that execute Python code and rewrite the backend's own source.
This is intentional and central to the project's design, but it creates an
unusual attack surface that a security reviewer should understand explicitly.

### What the agent can do at runtime

The agentic loop (see `chat_handler.py`) exposes these tools to the model:

- **`code_run`** — execute arbitrary Python in a subprocess.
- **`safe_write` / `js_safe_write`** — rewrite backend `.py` / plugin `.js`
  source, gated by syntax-check + import-verification + auto-rollback.
- **`tool_create`** — write a new Python tool to `custom_tools/` and hot-load
  it for the next turn.
- **`code_write`** — write arbitrary files under the vault root.
- **`git_rollback`** — `git checkout` backend source from HEAD.
- **Procedure code steps** — `.md` procedures may embed `<code>` blocks that
  `step_gate_runtime._execute_code_step` runs in a subprocess.

The agent is, by design, able to modify its own source code at runtime.

### Mitigations in place

- **Subprocess isolation** — all LLM-authored code runs in a child process,
  not in the backend's event loop. A crash, hang, or OOM in the child does
  not bring down the backend.
- **Secret-scrubbed environment** — `subprocess_utils.scrubbed_env()` strips
  every env var matching `*_API_KEY` / `*_SECRET` / `*_TOKEN` /
  `*_PASSWORD` / `*_PASSPHRASE` / `*_CREDENTIAL` before the child runs, so
  LLM-authored code cannot read or exfiltrate provider credentials. This is
  pattern-based (not a hardcoded list) so new providers added to
  `providers.json` / `.env` are auto-protected.
- **Resource limits** (POSIX) — `subprocess_utils.resource_limits_preexec`
  caps the child's address space (`RLIMIT_AS`, default 512 MiB), CPU
  seconds (`RLIMIT_CPU`, default 15s), and process count (`RLIMIT_NPROC`,
  default 64) to stop runaway allocation, busy loops, and fork bombs.
  Tunable via `VAULTBOT_CODE_RUN_MEM_MB` / `VAULTBOT_CODE_RUN_CPU_SECONDS` /
  `VAULTBOT_CODE_RUN_NPROC`. No-op on Windows (see Roadmap).
- **Wall-clock timeout** — every subprocess call passes a `timeout=` so a
  hang is killed rather than left indefinitely.
- **Import verification + auto-rollback** — `safe_write` rejects edits that
  break the backend's import graph and restores the original from a `.bak`
  backup, so a self-edit cannot brick the server on restart.
- **Session log redaction** — `session_logger._redact` replaces
  secret-shaped values with `[REDACTED]` before they hit the JSONL log, so
  credentials don't land on disk in log files.
- **CORS** — the backend binds localhost and sets `allow_origins=["*"]`
  with `allow_credentials` intentionally disabled (no cookies / auth
  headers). The Obsidian plugin talks to it directly.

### Residual risk

This is **not** a full sandbox. The child process has:

- **Filesystem access** — the agent can read/write the vault and (via
  `code_write`) the backend source tree.
- **Network access** — a malicious or prompt-injected turn could exfiltrate
  vault files to an attacker-controlled endpoint.

The mitigations raise the bar (the agent cannot see credentials, cannot
fork-bomb the host, cannot hang forever, cannot brick itself silently),
but a determined attacker who can inject prompts into the agent could still
exfiltrate vault contents. VaultBot is designed for a single trusted user
running it on their own machine; it is **not** safe to expose the backend
to untrusted users or to a network-reachable address.

### Roadmap (not yet implemented)

- **Windows Job Object** — `resource_limits_preexec` is a no-op on Windows
  because the `resource` module is POSIX-only. A Job Object wrapper would
  give Windows the same mem/CPU/process caps. Today the wall-clock timeout
  is the only Windows limit.
- **Full sandbox** — seccomp (Linux), App Sandbox / Seatbelt (macOS), and
  Job Object (Windows) would deny filesystem write outside the vault and
  block network entirely for `code_run` / procedure steps. This is the
  next major hardening item; tracked separately.
- **`git_rollback` audit** — the agent can currently `git checkout` backend
  source with no confirmation or audit step. A future change should require
  a logged confirmation before restoring source.