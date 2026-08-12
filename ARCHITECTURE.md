# VaultBot Architecture

A high-level map of how VaultBot works, for contributors who want to
understand the system before reading the code.

## Overview

VaultBot is a **personal AI research agent** that lives inside an Obsidian
vault. It's not a chatbot — it's a system that treats the vault as its
long-term memory, researches knowledge gaps autonomously, writes permanent
sourced notes, and can improve its own code.

```
┌─────────────────────────────────────────────────────────┐
│                    Obsidian Vault                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │  User/   │  │ Knowledge│  │  vaultbot_stuff/      │  │
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

### Self-Improvement (`self_improver.py`)
- `code_read` / `code_write` / `code_run` — read, write, test code
- `safe_write` — multi-stage verification before writing backend code:
  1. AST syntax check
  2. Subprocess import-graph verification (imports main.py with edit)
  3. Pytest gate
  4. Auto-rollback on failure
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

## Directory Map

```
vaultbot_backend/
├── main.py              # FastAPI app, lifespan, startup
├── chat_handler.py      # Agentic chat loop (the core)
├── agent_tools.py        # Tool definitions + system prompt
├── self_improver.py      # Code read/write/run, safe_write, tool_create
├── vault_indexer.py      # FAISS index management
├── vault_graph.py        # Wikilink graph
├── fused_retrieval.py    # Combined vector + graph retrieval
├── research_engine.py    # Multi-round web research
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
├── routers/              # FastAPI route handlers
├── tests/                # pytest suite (40+ test files)
├── sessions/             # JSONL session logs (gitignored)
├── checkpoints/          # Research cycle checkpoints (gitignored)
└── trash/                # Backups before overwrite/delete (gitignored)
```

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

- `vaultbot_stuff/System/` — living architecture notes in the vault
- `vaultbot_stuff/CONTRIBUTING.md` — development setup and conventions
- `vaultbot_stuff/SECURITY.md` — vulnerability reporting
- `/memories/repo/` — 80+ incident reports and design decisions
