# VaultBot Architecture

A high-level map of how VaultBot works, for contributors who want to
understand the system before reading the code.

## Project Mission

**The goal: sustainable AI inference with provenance.**

VaultBot is a proof-of-concept that frontier cloud models can be made
obsolete by programming well around small models. The two pillars:

1. **Sustainable inference** — a ~4B local model + deterministic procedures
   + a vault of sourced knowledge can handle most tasks at a fraction of
   the energy cost of a 70B cloud model. Every procedure that moves from
   the big model to the small model is a permanent energy saving.
2. **Provenance** — every knowledge claim in the vault is sourced and
   traceable. The vault is the knowledge base, not the model's training
   data.

**This is the goal, not the current state.** The architecture described
in this document is the *scaffolding* for that goal. The proof itself — a
longitudinal benchmark showing the small model + procedures matching a
frontier model on real workloads — does not exist yet. The procedure
library is small, the retrieval golden-set is ~30–50 queries, and the big
model still fires for most non-trivial work. The mission is the direction;
the work is getting there.

See
[`Knowledge/Architecture/VaultBot-Strategic-Vision.md`](Knowledge/Architecture/VaultBot-Strategic-Vision.md)
for the full strategic vision.

## Design Rule: No Bespoke Heuristics

VaultBot does not treat one-off heuristics as architecture. Language is too
variable for fragile keyword tricks, and edge cases grow over time.

Contributor rule:

1. Do not add ad-hoc phrase matching as policy logic.
2. Prefer deterministic and inspectable mechanisms: typed inputs, validated
  procedure contracts, retrieval/ranking with explicit scoring, and tests.
3. When uncertainty remains, fail loudly or escalate rather than silently
  guessing.

This keeps behavior stable as phrasing changes and makes failures debuggable.

## Overview

VaultBot is a **personal AI research agent** that lives inside an Obsidian
vault. It's not a chatbot — it's a system that treats the vault as its
long-term memory, researches knowledge gaps autonomously, writes permanent
sourced notes, and can improve its own code.

```
┌─────────────────────────────────────────────────────────┐
│                    Obsidian Vault                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │  User/   │  │ Knowledge│  │  vaultbot/           │  │
│  │  (notes) │  │  /Research│  │  ┌────────────────┐  │  │
│  └──────────┘  └──────────┘  │  │ vaultbot_backend│  │  │
│                               │  │  (Python)       │  │  │
│  ┌──────────────────────────┐ │  └────────────────┘  │  │
│  │  .obsidian/plugins/      │ │  ┌────────────────┐  │  │
│  │  vaultbot/ (JS plugin)   │ │  │ Memory/Chat/    │  │  │
│  └──────────────────────────┘ │  │ System/         │  │  │
│                               │  └────────────────┘  │  │
└─────────────────────────────────────────────────────────┘
```

## The Two Processes

VaultBot runs as **two processes** that communicate over a local WebSocket:

### 1. Obsidian Plugin (`main.js`)
- Lives in `.obsidian/plugins/vaultbot/`
- Spawns and manages the Python backend
- Renders the chat UI in Obsidian's sidebar
- Handles model download, settings, self-update
- Starts an MCP server so external tools (VS Code Copilot, Claude) can use
  VaultBot's research tool

### 2. Python Backend (`vaultbot_backend/`)
- FastAPI server on `127.0.0.1:8000`
- Agentic chat loop with tool-calling (Ollama/OpenAI-compatible)
- FAISS vector index for vault search
- Autonomous background researcher
- Self-improvement engine (code editing, tool creation)

## Data Flow: A Chat Turn

```
User types in Obsidian sidebar
        │
        ▼
Plugin sends JSON over WebSocket to /ws
        │
        ▼
chat_handler.handle_chat()
  ├─ Fused retrieval: vector search + wikilink graph walk
  ├─ Build system prompt (identity + procedures + vault context)
  ├─ Agentic loop:
  │   ├─ Send to LLM (Ollama or cloud API)
  │   ├─ LLM returns text or tool_call
  │   ├─ If tool_call: execute tool, feed result back
  │   └─ Repeat until LLM emits final answer
  ├─ Stream answer chunks to plugin via WebSocket
  └─ Post-turn: regenerate self-model, run QA worker
```

## Key Subsystems

### Retrieval (`fused_retrieval.py`, `vault_indexer.py`, `vault_graph.py`)
- **FAISS index**: nomic-embed-text embeddings of all vault notes
- **Wikilink graph**: Obsidian's `[[links]]` form a graph; retrieval walks
  it to pull connected context
- **Fused**: combines vector similarity + graph proximity for context that's
  both relevant AND connected

### Research Engine (`research_engine.py`, `free_search.py`)
- Multi-engine web search (DuckDuckGo, Marginalia, arXiv) — no API keys
- Multi-round dig with gap detection and follow-up queries
- LLM synthesis of all sources into a single sourced note
- Wikipedia blocked at every layer per operator directive

### Autonomous Researcher (`autonomous_researcher.py`)
- Background thread that scans for knowledge gaps (dangling wikilinks, thin
  notes)
- Voyager-style curriculum ranks gaps by learning value
- Researches gaps autonomously, writes notes, re-indexes
- Checkpointed: survives crashes and resumes

### Self-Improvement (`self_improver.py`, `safe_writer.py`, `code_verify.py`)
- `code_read` / `code_write` / `code_run` — read, write, test code
- `safe_write` (in `safe_writer.py`) — multi-stage verification before
  writing backend code:
  1. AST syntax check
  2. Subprocess import-graph verification (`code_verify.py`)
  3. Pytest gate
  4. Auto-rollback on failure
- `js_safe_write` (in `safe_writer.py`) — same pattern for JS files
  (node --check + require() load test)
- `tool_create` — agent can write new tools in `custom_tools/`
- `git_rollback` — restore from git HEAD

### Safety Layers
- **`vault_guard.py`**: blocks writes to sacred journals (date-only
  filenames) and LOCKED notes
- **`safe_mode.py`**: Safe Mode (default) disables self-modification tools;
  Developer Mode unlocks them
- **`auth.py`**: shared-secret token between plugin and backend prevents
  other local processes from hijacking the API
- **`rate_limit.py`**: token-bucket rate limiter on all endpoints
- **`subprocess_utils.py`**: `scrubbed_env()` strips API keys from any
  subprocess the agent spawns
- **`session_logger.py`**: append-only JSONL audit trail with automatic
  secret redaction
- **`session_log_reader.py`**: standalone CLI reader for session logs
  (`python -m session_log_reader read <uuid|latest|title>`), works
  without a running backend. See
  [`docs/SESSION-LOG-SCHEMA.md`](docs/SESSION-LOG-SCHEMA.md) for the
  full event schema, field reference, and category mapping.

### Identity (`identity.py`)
- `IDENTITY.md`: stable self-concept (human-seeded, rarely changes)
- `SELF_MODEL.md`: MIRROR-style reconstructive narrative, regenerated each
  turn, ≤3000 tokens
- The vault is the mind; the LLM is swappable plumbing

### Provider Registry (`providers.py`)
- Single "pot" of LLM connections: any provider (Ollama, OpenAI, OpenRouter)
  can serve any role (big, small, vision)
- Auto-migrates from legacy `.env` vars on first run
- Persisted to `providers.json` (gitignored)

## Repository Architecture: The Baseline Membrane

VaultBot lives in a **single repo** that serves two roles: it's the public
baseline anyone downloads, AND it's each user's personal vault that grows
with them. The `.gitignore` keeps personal data out of git, and a
`baseline` frontmatter field draws the line for the gray zone.

```
┌──────────────────────────────────────────────────────────┐
│                 A User's Working Vault                     │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  COMMITTED (shipped to everyone)                   │   │
│  │  • vaultbot_backend/*.py    (all backend code)     │   │
│  │  • System/**/*.md           (baseline: true only)  │   │
│  │  • .obsidian/plugins/       (plugin source)        │   │
│  │  • baseline/                (directive templates)   │   │
│  │  • Root docs                (README, LICENSE, etc) │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  LOCAL ONLY (gitignored or baseline: false)       │   │
│  │  • Knowledge/, Memory/, User/    (personal notes) │   │
│  │  • identity/, sessions/, .env   (personal data)   │   │
│  │  • System/Procedures/My-Workflow.md  (bespoke)    │   │
│  │  • System/Identity/Sean-*.md    (personal)        │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

**The membrane is enforced at three layers:**

1. **Pre-commit hook** (`check_baseline_markers.py`) — blocks commits of
   `System/` `.md` files that lack `baseline: true`.
2. **`submit_contribution` tool** — filters changed files to baseline-only
   before creating a PR.
3. **`review_contributions` tool** — flags missing markers during PR review
   as a safety net.

Backend `.py` files are always baseline — no marker needed. The membrane
only applies to `vaultbot/System/` `.md` files, which is where the
ambiguity between "general-purpose" and "personal" lives.

## Directory Map

```
vaultbot_backend/
├── main.py              # FastAPI app, lifespan, startup
├── chat_handler.py      # Agentic chat loop entry point (thin orchestrator)
├── chat_context.py      # Token cap, tool-result aging, history sanitization
├── chat_preflight.py    # Trivial-turn shortcut, procedure routing hints
├── chat_tool_dispatch.py # Tool execution switch (vault_search, code_read, etc.)
├── chat_helpers.py      # Progress events, notifications, heartbeat, truncation
├── agent_tools.py        # Tool definitions + system prompt
├── self_improver.py      # Code read/write/run, tool_create, git_rollback
├── code_verify.py        # Subprocess import/pytest/startup verification
├── safe_writer.py        # safe_write + js_safe_write with AST + import checks
├── vault_indexer.py      # FAISS index management
├── vault_graph.py        # Wikilink graph
├── fused_retrieval.py    # Combined vector + graph retrieval
├── research_engine.py    # Multi-round web research orchestration
├── text_scoring.py       # Pure text scoring (keyterms, sentence scoring)
├── source_classification.py # URL classification (blocked, academic, relevance)
├── autonomous_researcher.py  # Background gap-filling
├── identity.py           # IDENTITY.md + SELF_MODEL.md management
├── providers.py          # Provider/model registry
├── session_logger.py     # JSONL audit trail
├── vault_guard.py        # Sacred/locked note protection
├── safe_mode.py          # Safe Mode / Developer Mode gate
├── auth.py               # Plugin-backend shared-secret auth
├── rate_limit.py         # Token-bucket rate limiter
├── subprocess_utils.py   # Console-free subprocess + secret scrubbing
├── config.py             # All tunable constants (TUNABLES singleton)
├── services.py           # Services dataclass for DI
├── app_state.py          # FastAPI dependency injection
├── custom_tools/         # Agent-authored tools (auto-loaded)
│   └── parsers/          # HTML/PDF/markdown/text parsers (used by textbook_ingest)
├── routers/              # FastAPI route handlers
├── tests/                # pytest suite (40+ test files)
├── sessions/             # JSONL session logs (gitignored)
├── checkpoints/          # Research cycle checkpoints (gitignored)
└── trash/                # Backups before overwrite/delete (gitignored)
```

## Note on the Obsidian Plugin (`main.js`)

The Obsidian plugin (`.obsidian/plugins/vaultbot/main.js`) is a single
~4,000-line hand-written CommonJS file. It uses `require()` /
`module.exports` with no bundler, no `package.json`, no build step —
Obsidian loads it directly.

**Why it hasn't been split:** Splitting it requires introducing a JS
bundler (esbuild or rollup) and ideally TypeScript. This is a Python-first
project with zero JS build tooling. Adding `node_modules/`, a build step,
and a dev dependency chain to split one file is a build pipeline change,
not a code organization change. The file is large but works and ships.

**The path to splitting it** (for anyone who wants to take it on):
1. Add `esbuild` + `tsconfig.json` + `package.json`
2. Move `main.js` to `src/main.ts`, split into `src/ws.ts`, `src/ui.ts`,
   `src/settings.ts`
3. Compile to `main.js` via esbuild
4. Add a build step to the release process

This is a dedicated project, not a quick refactor.

## Design Principles

1. **The vault is the mind.** The LLM is swappable plumbing. Notes, links,
   and shared history are what the system actually knows.
2. **Fail loud.** No silent fallbacks. If something breaks, it breaks
   visibly. Every `except` block must justify itself.
3. **Deterministic where possible.** Search, fetch, clean, and verification
   are deterministic. The LLM is only used for synthesis and reasoning.
4. **Defense in depth.** File writes, code edits, subprocess execution, and
   network access all have multiple independent safety layers.
5. **Token economy.** The small model handles classification/routing.
   Procedures shift repeat work from expensive models to cheap ones.
   Context is aggressively budgeted.

## Further Reading

- `vaultbot/System/` — living architecture notes in the vault
- `vaultbot/CONTRIBUTING.md` — development setup and conventions
- `vaultbot/SECURITY.md` — vulnerability reporting
- `/memories/repo/` — 80+ incident reports and design decisions
