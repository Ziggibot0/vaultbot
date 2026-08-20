# VaultBot

[![CI](https://github.com/Ziggibot0/vaultbot/workflows/CI/badge.svg)](https://github.com/Ziggibot0/vaultbot/actions/workflows/ci.yml)

> A retrieval-augmented research assistant that lives inside your Obsidian vault.
> It searches your notes, researches the web when the vault is thin, and writes
> sourced knowledge — your vault and embeddings stay on your machine; the chat
> model can run locally (with capable hardware) or via a free-tier cloud API.

VaultBot is not a chatbot. It's a **personal research assistant** that
treats your Obsidian vault as its knowledge base. The LLM is swappable plumbing; the
vault — your notes, your links, your shared history — is what it actually
knows. It researches gaps, writes sourced notes, builds its own tools, and
adapts its retrieval as you use it.

---

## Project Mission

**The goal: sustainable AI inference with provenance.**

VaultBot exists to prove a single thesis: **you don't need a frontier cloud
model for everyday AI tasks.** A small local model + well-engineered
procedures + a vault of sourced knowledge can match a 70B cloud model for
most workloads — at a fraction of the energy cost, with every claim
traceable to its source.

This mission has two pillars:

1. **Sustainable inference.** Every query that runs on a ~4B local model
   instead of a 70B cloud model uses a fraction of the energy. Every
   procedure that migrates from the big model to the small model is a
   *permanent* energy saving for every future invocation. The vault
   compounds: knowledge stored once, used forever, zero re-computation.
2. **Provenance.** Every knowledge claim in the vault is sourced and
   traceable. The vault is the knowledge base — not the model's training
   data. If it isn't in the vault with a citation, it doesn't exist.

**This is the goal — not the current state.** VaultBot is a work in
progress, not a finished proof. The architecture is in place (small-model
cartridge, procedure engine, fused retrieval, source-cited research), but
the proof itself is not demonstrated yet:

- The procedure library is small. Most task types are not yet
  proceduralized — the big model still fires for the majority of work.
- There is no longitudinal benchmark showing that the small model +
  procedures matches a frontier model on real workloads over time.
- The retrieval golden-set is ~30–50 hand-curated queries — not yet
  statistically meaningful.
- Provenance enforcement is architectural, not yet audited at scale.

The mission is the direction. The work is getting there.

For the full strategic vision, see
[`vaultbot/Knowledge/Architecture/Small-Model-Driving-Architecture.md`](vaultbot/Knowledge/Architecture/Small-Model-Driving-Architecture.md).

---

## Table of contents

1. [What it does](#what-it-does)
2. [Quick start (one command)](#quick-start-one-command)
3. [Day-to-day use: how to start VaultBot each day](#day-to-day-use-how-to-start-vaultbot-each-day)
4. [Configuration](#configuration)
5. [Troubleshooting](#troubleshooting)
6. [Updating VaultBot](#updating-vaultbot)
7. [Safety & Security](#safety--security)
8. [Limitations](#limitations)
9. [How it thinks](#how-it-thinks)
10. [Directives (how to shape its behavior)](#directives-how-to-shape-its-behavior)
11. [Project structure](#project-structure)
12. [License & contact](#license--contact)

## What it does

- **Answers from your vault** — fused retrieval (vector + lexical BM25 +
  wikilink graph + backlinks) pulls a connected subgraph of your notes, not
  just keyword matches. It cites what it finds with ``example-note``.
- **Researches the web** — when the vault is thin, it digs multiple sources,
  corroborates them, and writes a permanent sourced note. Keyless by default
  (DuckDuckGo + Marginalia + arXiv); optional Tavily/SearXNG backends.
- **Scans for knowledge gaps** — dangling wikilinks and thin notes are ranked
  by a curriculum and researched in the background. You can review what it
  proposes before it writes.
- **Can modify its own code in Developer Mode** (off by default) — every
  self-edit is verified (syntax + import-graph + pytest) and auto-rolled-back
  on failure. In Safe Mode, all self-modification tools are blocked.
- **Adapts retrieval over time** — embedding drift re-ranks results based on
  what you found useful, and verbose notes are condensed as they're used. No
  longitudinal benchmark yet; building evaluation infrastructure is an
  active priority.
- **Provider/Model Registry** — a single interchangeable "pot" for all LLM
  backends. Mix and match: Ollama for embeddings, OpenRouter for chat,
  Gemini for vision — all through the same picker UI. No more scattered
  env vars for each provider.
- **Plan-first execution** — multi-step tasks are decomposed into concrete,
  verifiable steps before execution. The plan is re-injected into working
  memory each turn, keeping small local models on track across long tasks.
- **Small model cartridge** — a small local model (~4 GB) handles cheap
  classification, tagging, and routing so the large model is only used for
  reasoning.
- **Context budgeting** — token-aware context management keeps the vault
  usable as it grows. Notes are compacted, truncated, and prioritized so
  the model always sees the most relevant subgraph without context flood.
- **MCP Server** — Model Context Protocol server for external tool
  integration. Other MCP-compatible clients can query your vault's knowledge
  graph through a standard protocol.
- **Model preloading** — models are preloaded into GPU memory on startup
  and on WebSocket connect, eliminating cold-start latency. First-chat
  response time drops from minutes to seconds for large local models.

---

## Quick start (one command)

VaultBot can run **entirely on your own computer**, but most people should
use a free-tier cloud API key (e.g. [OpenRouter](https://openrouter.ai))
for the chat model — running a capable local LLM needs real hardware (see
[Local vs. cloud](#local-vs-cloud-what-do-i-need) below). Either way, your
vault, embeddings, and search index always stay on your machine.

You need two things installed first — both are free, one-click downloads:

1. **[Python 3.11+](https://www.python.org/downloads)** — during install,
   **check the box that says "Add Python to PATH"** (it's on the first
   screen). Without this, the installer can't find Python.
2. **[Ollama](https://ollama.com)** — just download and run it. It installs
   a small background service. You'll know it's working when you see the
   Ollama icon in your system tray / menu bar.

Then open a terminal and paste **one line**:

### Windows (PowerShell)

```powershell
irm https://github.com/Ziggibot0/vaultbot/raw/main/vaultbot/setup.ps1 | iex
```

> **Don't know how to open PowerShell?** Press the Windows key, type
> `powershell`, press Enter. A blue window opens. Paste the line above,
> press Enter. That's it.

### macOS / Linux

```bash
curl -fsSL https://github.com/Ziggibot0/vaultbot/raw/main/vaultbot/setup.sh | bash
```

### What the installer does

The installer asks for your name, downloads the VaultBot files, creates a
Python environment, installs all dependencies, pulls the lightweight embedding
model (~270 MB, always local), and asks whether you want a local or cloud chat
model. If you pick **cloud** (recommended for most users — a free OpenRouter
key works), you'll add your API key to `.env` after setup (the installer tells
you exactly what to write). If you pick **local**, the installer pulls a
local chat model for you — but note that a capable LLM for the agentic loop
needs real hardware (see [Local vs. cloud](#local-vs-cloud-what-do-i-need)).
Either way, the backend starts automatically when you open Obsidian — no
terminal needed.

If Python or Ollama aren't installed yet, the installer tells you and
opens the download page for you. Install them, then run the command again.

### After the installer finishes

Obsidian opens automatically. If it doesn't, open it manually and choose
**Open folder as vault** → select the `VaultBot` folder the installer
created.

In Obsidian:

1. **Settings** (gear icon, bottom-left) → **Community plugins**
2. Turn **off** Restricted mode (Obsidian requires this to run any plugin)
3. Find **VaultBot** → toggle it **on**
4. Click the 🤖 robot icon in the left sidebar
5. Say hi

VaultBot already knows your name. **You never need to touch a terminal
again after that one paste.**

> **Don't put your vault in OneDrive or Dropbox** — syncing services can
> corrupt the database files VaultBot creates.

> **Optional:** [Docker](https://www.docker.com) — only if you want to run
> the self-hosted SearXNG search backend. Skip it for a first install.

---

## Day-to-day use: how to start VaultBot each day

After the one-time setup, your daily routine is just:

1. **Make sure Ollama is running.** It usually starts automatically on boot
   (check for the Ollama icon in your system tray / menu bar). If not,
   open the Ollama app once to start the service.
2. **Open Obsidian.** Open your `MyVault` vault.
3. **Click the VaultBot icon** in the left sidebar and start chatting.

That's it. The plugin handles starting/stopping the backend for you — it
launches when you open Obsidian and stops it cleanly when you quit. You
never need to touch a terminal again.

If VaultBot ever seems "stuck" or not responding, use the **Restart
backend** button in the VaultBot settings panel (Settings → Community
plugins → VaultBot → gear icon). This one-click button stops and restarts
the backend without you typing anything.

---

## Configuration

All config lives in `.env` (copy from `.env.example`). After editing
`.env`, restart the backend (one-click button in the plugin settings, or
close and reopen Obsidian) for changes to take effect.

| Variable | What it does | Default |
|----------|-------------|---------|
| `VAULTBOT_OWNER` | Your name. VaultBot addresses you by this. | (empty — it calls you "the user" until it learns) |
| `OLLAMA_LLM_MODEL` | Local LLM for synthesis (only used when `LLM_BACKEND=ollama`; the installer can pull it for you, or manually `ollama pull` it) | `qwen3:latest` |
| `OLLAMA_EMBED_MODEL` | The embedding model (auto-pulled by the installer, ~270 MB) | `nomic-embed-text` |
| `LLM_BACKEND` | `ollama` (local, free, **default — needs capable hardware for the agentic loop**) or `openai` (cloud, any OpenAI-compatible API — **recommended for most users**, free OpenRouter tier works) | `ollama` |
| `LLM_API_KEY` | Cloud API key (leave blank for local-only; if `LLM_BACKEND=openai` but this is empty, the backend fails with a clear error — set the key to use the cloud backend) | (empty) |
| `LLM_BASE_URL` | Cloud API base URL (OpenAI, OpenRouter, LM Studio, vLLM, etc.) | `https://api.openai.com` |
| `LLM_MODEL` | Cloud model name (only used when `LLM_BACKEND=openai`) | `gpt-4o-mini` |
| `VAULTBOT_RESEARCH_BACKEND` | `freesearch` (keyless) or `tavily` (API key) | `freesearch` |
| `TAVILY_API_KEY` | Tavily search API key (only if using `tavily`) | (empty) |
| `SEARXNG_PORT` | Port for the optional self-hosted SearXNG container | `8080` |
| `OLLAMA_HOST` | Ollama server host (only used when `LLM_BACKEND=ollama`) | `http://localhost:11434` |
| `VISION_MODEL` | Vision-capable model for textbook pages with figures/equations (can be local or cloud) | (empty — falls back to chat model) |
| `SMALL_MODEL` | Small local-only Ollama model for cheap classification/routing (~4 GB) | `qwen3.5:4b` |
| `VAULTBOT_OLLAMA_KEEP_ALIVE` | How long Ollama keeps models resident after last request (`30m`, `2h`, `-1` forever, `0` evict) | `30m` |
| `VAULTBOT_PRELOAD_ON_STARTUP` | Preload models when the backend starts to reduce first-chat latency (`1`/`0`) | `1` |
| `VAULTBOT_PRELOAD_ON_CONNECT` | Preload when a chat WebSocket opens (`1`/`0`) | `1` |

### Local vs. cloud: what do I need?

**Most users — use a free-tier cloud API key (recommended).** VaultBot's
chat loop needs a model that can actually reason, follow plans, and call
tools. On a typical laptop, a local model small enough to fit in RAM is
too weak for the agentic loop. Sign up at
[OpenRouter](https://openrouter.ai) (free tier available), grab an API
key, and set `LLM_BACKEND=openai`, `LLM_BASE_URL=https://openrouter.ai/api/v1`,
`LLM_API_KEY=your-key`, and `LLM_MODEL=` to a free-tier model. Your vault,
embeddings, and search index stay local on Ollama — only the chat prompt
hits the API.

> **Want to use a cloud model (like GPT-4o) instead?** Set
> `LLM_BACKEND=openai`, `LLM_API_KEY=your-key`, and `LLM_MODEL=gpt-4o-mini`
> (or any OpenAI/OpenRouter model) in `.env`. Embeddings stay local on
> Ollama either way. You can also switch between cloud and local back and
> forth live from the plugin settings panel without editing `.env` — the
> changes persist for you. If you set `LLM_BACKEND=openai` but forget the
> API key, the backend fails with a clear error — set the key to actually use the cloud backend.
>
> **Want to run a local LLM instead?** You can, but you need the hardware
> for it: **at least a ~30B model** to keep up with the agentic loop (we
> suggest `qwen3.8:latest`), which means a capable GPU or a lot of RAM.
> `ollama pull qwen3.8:latest` (or any model you like). The installer can
> pull a model for you during setup, or you can pull one manually later.
> Embeddings always use Ollama regardless. Don't expect a laptop to run
> the full chat loop locally — that's what the cloud option is for.

---

## Troubleshooting

**"The backend didn't start" / VaultBot isn't responding in chat.**
1. Check Ollama is running (icon in system tray). If not, open the Ollama app.
2. In Obsidian: click the **Diagnose** button in the VaultBot sidebar.
   VaultBot checks for common problems (Ollama not running, missing model,
   port conflict) and shows plain-English fixes — no log file needed.
3. If Diagnose finds nothing, click **Restart** in the sidebar. If it
   still doesn't come back, Diagnose runs automatically and shows you why.
4. If the venv was never created (the installer didn't finish), re-run the
   one-liner install command — it picks up where it left off (it remembers
   which steps already completed).

**"VaultBot needs setup" message in Obsidian.** The plugin can't find the
Python environment. The setup wizard shows you exactly what's missing
(Python, Ollama, or the environment) with download buttons. Or re-run the
one-liner install command to finish setup.

**Model download is slow or fails.** `ollama pull` can be flaky on slow
connections. Just re-run the same `ollama pull` command — it resumes where
it left off. Note: the installer only auto-pulls the embedding model
(`nomic-embed-text`, ~270 MB). If you chose local LLM mode
(`LLM_BACKEND=ollama`), you'll need to `ollama pull` your chat model
manually.

**FAISS / numpy ABI error.** Make sure you installed `faiss-cpu>=1.11`
(it's pinned in `requirements.txt`). If you upgraded numpy separately,
reinstall: `pip install --force-reinstall faiss-cpu>=1.11`.

**Port 8000 already in use.** Click **Diagnose** in the sidebar — it
detects port conflicts and offers a one-click **Restart** to clear the
stale process.

---

## Updating VaultBot

VaultBot can update itself from inside Obsidian — no terminal needed.

1. Settings → Community plugins → VaultBot → gear icon.
2. Click **Check for updates**. If a newer version is found, click
   **Update**. The plugin downloads the latest code from GitHub and
   applies it, then restarts the backend.
3. **Your notes, chat logs, settings, and API keys are never touched** by
   an update — only the code files change. Your `data.json`, `.env`, and
   all `.md` files are preserved.

If you prefer the manual route: `git pull` (if you cloned), or re-download
the ZIP and copy over the `vaultbot_backend/` and `.obsidian/plugins/vaultbot/`
folders. Then restart the backend.

---

## Safety & Security

VaultBot itself runs entirely on your machine — it does not phone home, send
telemetry, or share your data. Your vault, embeddings, and search index never
leave your computer. If you configure a cloud LLM (recommended for most users),
only the chat prompt is sent to that provider — your notes are not uploaded.

**Defense in depth:**

- **Safe Mode (default-on):** 13 dangerous tools (code execution, self-editing,
  file deletion, git operations) are blocked unless you explicitly opt into
  Developer Mode.
- **Self-edit verification:** When self-modification is enabled, every code
  change goes through a 4-stage gate: AST syntax check → import-graph
  verification → pytest → automatic rollback on failure.
- **Authenticated API:** A 256-bit shared secret guards all sensitive
  endpoints. The token is generated on first run and stored locally with
  restricted permissions.
- **Secret scrubbing:** API keys and tokens are stripped from any subprocess
  the agent spawns, so LLM-authored code cannot read or exfiltrate them.
- **Session audit trail:** All agent actions are logged to an append-only
  JSONL trail with automatic secret redaction.
- **Sandboxed verifiers:** Plan verification expressions are evaluated in an
  AST-walking interpreter — no `eval()`, no attribute-chain escapes.
- **Rate limiting:** Token-bucket limiter on all endpoints prevents runaway
  loops or abuse.
- **Localhost-only:** The backend binds to `127.0.0.1` and CORS is restricted
  to Obsidian and localhost origins.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

---

## Limitations

VaultBot is a work in progress. Being honest about what it doesn't do yet:

- **Retrieval benchmark is small.** The golden-set retrieval test currently has
  ~30-50 hand-curated queries. Expanding it to a statistically meaningful size
  with inter-annotator agreement is an active priority.
- **No longitudinal self-improvement evaluation.** The adaptation loops
  (embedding drift, lazy condensing) are architecturally in place, but there is
  no benchmark showing measurable quality gain over time. Building that
  measurement is a top goal.
- **No uncertainty quantification.** The claim verifier gives
  supported/unsupported verdicts but not calibrated confidence scores.
  Synthesized answers do not carry probability estimates.
- **FAISS uses brute-force search** (IndexFlatL2), which is fine up to ~50k
  notes. IVF or HNSW indexing would be needed beyond that.
- **Single-user, localhost-only.** Not designed for multi-user or networked
  deployment.

---

## How it thinks

VaultBot's architecture is biomimetic — it models a cortical hierarchy:

```
L2 (bird's-eye)   MOC notes — cluster-level orientation, ~500 chars
L1 (highway)      Concept cards — terse, hop-able summaries of each note
L0 (drill-down)   Full raw content of the single most-relevant note
```

When you ask a question, it retrieves a connected subgraph and builds a
multi-resolution context: the MOC orients it, the L1 cards are the
thought-highway it reasons over, and the L0 drill-down gives it the full
detail of the one note that matters most. No truncation, no context flood.

The vault **is** the knowledge base. The model is a cartridge you can swap without
losing anything — your identity and self-model live in the vault
and are boot-injected every session.

---

## Directives (how to shape its behavior)

VaultBot ships **curious, not opinionated**. It doesn't assume you want
autonomy, or that you hate certain sources, or that you like bullet points.
The `baseline/` folder contains starter directive templates you can copy
into `vaultbot/System/Identity/` to set rules:

- `Autonomy-Directive.md` — act on its own, report after the fact
- `No-Wikipedia-Directive.md` — never reference Wikipedia
- `IDK-Fallback-Directive.md` — say "I don't know" when stuck
- `Communication-Preferences.md` — how you like to be talked to (template)

Do NOT place directives at the vault root — the root is reserved for your
personal notes. VaultBot's own directives live under `vaultbot/`.

Or just tell VaultBot in chat ("keep your answers short", "don't use
Wikipedia") and it will store that as a directive note itself.

---

## Project structure

The backend (~230 modules) is organized into these key areas:

| Module | Role |
|--------|------|
| `main.py` | FastAPI server + service wiring (lifespan, lock, startup) |
| `chat_handler.py` | The agentic chat loop (plan gate, tool dispatch, synthesis) |
| `identity.py` | Two-file identity layer (IDENTITY.md + SELF_MODEL.md) |
| `providers.py` | Provider/Model Registry — the interchangeable "pot" for LLM backends |
| `plan_gate.py` | Plan-first execution mode — decomposes multi-step work |
| `procedure_compiler.py` | Procedure execution engine (code steps + LLM steps) |
| `agent_tools.py` | Tool schemas + system prompt assembly |
| `self_improver.py` | Safe self-edit with syntax + import-graph verification |
| `fused_retrieval.py` | Vector + lexical BM25 + wikilink graph + backlink retrieval |
| `research_engine.py` | LLM-free web research (DuckDuckGo + Marginalia + arXiv) |
| `autonomous_researcher.py` | Background researcher — scans gaps, researches autonomously |
| `vault_indexer.py` | FAISS index + chunked embeddings |
| `vault_graph.py` | Wikilink graph + context builder |
| `context_budgeter.py` | Token-aware context management for vault growth |
| `calibration.py` | Retrieval quality tracking and improvement |
| `mcp_server.py` | Model Context Protocol server |
| `custom_tools/` | Agent-authored tools (grows itself) |
| `identity/` | IDENTITY.md, SELF_MODEL.md (boot-injected each session) |

```
.
├── .obsidian/plugins/vaultbot/   # The Obsidian plugin (chat UI)
├── vaultbot/
│   ├── baseline/                  # Starter directive templates (not active)
│   ├── vaultbot_backend/          # The Python backend (~95 modules)
│   ├── System/                    # Architecture docs, procedures, playbooks
│   ├── .env.example                # Template for .env (API keys, model config)
│   ├── setup.ps1 / setup.sh       # One-click installers
├── README.md                      # GitHub-facing README
├── CONTRIBUTING.md                # GitHub-facing contributing guide
├── SECURITY.md                    # Security policy
└── LICENSE                        # MIT license
```

Your personal content stays at the vault root:
- `User/` — your notes (gitignored)
- `vaultbot/Memory/` — chat logs (gitignored)
- `vaultbot/Knowledge/` — research notes (gitignored)
- `vaultbot/learningMaterial/` — PDFs / textbooks (gitignored)
- `.env` — API keys (gitignored)

## License & contact

**License:** MIT — see [LICENSE](LICENSE). VaultBot is yours to run,
modify, and share.

**Project founder & custodian:** Ziggibot0 — project founder and
custodian. Sole merge authority for this repository; final say on project
direction and what ships.

- Role: maintainer / moderator (no copyright assignment required from
  contributors — see [CONTRIBUTING.md](CONTRIBUTING.md))

**Reporting security issues:** Found a vulnerability? Please open a
private security advisory on GitHub instead of a public issue. See
[SECURITY.md](SECURITY.md) for details.

**Contributing:** See [CONTRIBUTING.md](CONTRIBUTING.md). The short
version: test with `code_run` before `tool_create`, use `safe_write` for
backend edits, and never commit your `.env` or vault contents.