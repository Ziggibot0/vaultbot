# VaultBot

> A self-improving AI research agent that lives inside your Obsidian vault.
> It thinks with your notes, researches the web, writes permanent
> knowledge, and grows itself — all while spending minimal LLM calls.

VaultBot is not a chatbot. It's a **personal intelligence system** that
treats your Obsidian vault as its mind. The LLM is swappable plumbing; the
vault — your notes, your links, your shared history — is what it actually
knows. It researches gaps, writes sourced notes, builds its own tools, and
gets smarter the more you use it.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Quick start (one command)](#quick-start-one-command)
3. [Day-to-day use: how to start VaultBot each day](#day-to-day-use-how-to-start-vaultbot-each-day)
4. [Configuration](#configuration)
5. [Troubleshooting](#troubleshooting)
6. [Updating VaultBot](#updating-vaultbot)
7. [How it thinks](#how-it-thinks)
8. [Directives (how to shape its behavior)](#directives-how-to-shape-its-behavior)
9. [Project structure](#project-structure)
10. [License & contact](#license--contact)

## What it does

- **Answers from your vault** — fused retrieval (vector + wikilink graph +
  backlinks) pulls a connected subgraph of your notes, not just keyword
  matches. It cites what it finds with `[[example-note]]`.
- **Researches the web** — when the vault is thin, it digs multiple sources,
  corroborates them, and writes a permanent sourced note. Keyless by default
  (DuckDuckGo + Marginalia + arXiv); optional Tavily/SearXNG backends.
- **Fills gaps autonomously** — a background researcher scans for dangling
  wikilinks and thin notes, ranks them by a Voyager-style curriculum, and
  researches them on its own.
- **Self-improves safely** — it can write new tools for itself and edit its
  own source code. Every self-edit is verified (syntax + import-graph check)
  and auto-rolled-back if it would break the backend.
- **Gets smarter over time** — four compounding loops: embedding drift
  (relevance feedback re-ranks retrieval), lazy condensing (notes de-fluff
  as you use them), concept-card refinement, and self-model regeneration.

---

## Quick start (one command)

VaultBot runs **entirely on your own computer** — nothing leaves your
machine unless you choose to add a cloud LLM later.

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
irm https://github.com/ziggibot-uni/vaultbot/raw/main/setup.ps1 | iex
```

> **Don't know how to open PowerShell?** Press the Windows key, type
> `powershell`, press Enter. A blue window opens. Paste the line above,
> press Enter. That's it.

### macOS / Linux

```bash
curl -fsSL https://github.com/ziggibot-uni/vaultbot/raw/main/setup.sh | bash
```

### What the installer does

The installer asks for your name, downloads the VaultBot files, creates a
Python environment, installs all dependencies, pulls the lightweight embedding
model (~270 MB), asks whether you want a local or cloud chat model (and
pulls a local model for you if you pick local), writes your config, and
opens Obsidian for you — all automatically. It takes 10–30 minutes the
first time (mostly downloads). You only do this once.

If you choose the cloud API path, you'll add your API key to `.env` after
setup (the installer tells you exactly what to write). If you choose
local, the installer pulls the model for you. Either way, the backend
starts automatically when you open Obsidian — no terminal needed.

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
| `OLLAMA_LLM_MODEL` | Local LLM for synthesis (only used when `LLM_BACKEND=ollama`; the installer can pull it for you, or manually `ollama pull` it) | `qwen3.6:latest` |
| `OLLAMA_EMBED_MODEL` | The embedding model (auto-pulled by the installer, ~270 MB) | `nomic-embed-text` |
| `LLM_BACKEND` | `ollama` (local, free, **default — zero-config**) or `openai` (cloud, any OpenAI-compatible API — recommended for laptops) | `ollama` |
| `LLM_API_KEY` | Cloud API key (leave blank for local-only; if `LLM_BACKEND=openai` but this is empty, the backend falls back to Ollama so it still starts) | (empty) |
| `LLM_BASE_URL` | Cloud API base URL (OpenAI, OpenRouter, LM Studio, vLLM, etc.) | `https://api.openai.com` |
| `LLM_MODEL` | Cloud model name (only used when `LLM_BACKEND=openai`) | `gpt-4o-mini` |
| `VAULTBOT_RESEARCH_BACKEND` | `freesearch` (keyless) or `tavily` (API key) | `freesearch` |
| `TAVILY_API_KEY` | Tavily search API key (only if using `tavily`) | (empty) |
| `SEARXNG_PORT` | Port for the optional self-hosted SearXNG container | `8080` |

> **Want to use a cloud model (like GPT-4o) instead of local?** Set
> `LLM_BACKEND=openai`, `LLM_API_KEY=your-key`, and `LLM_MODEL=gpt-4o-mini`
> (or any OpenAI/OpenRouter model) in `.env`. Embeddings stay local on
> Ollama either way. You can also switch between cloud and local back and
> forth live from the plugin settings panel without editing `.env` — the
> changes persist for you. If you set `LLM_BACKEND=openai` but forget the
> API key, the backend silently falls back to Ollama so it always starts —
> you'll see a note in the Diagnose panel.
>
> **Want to run a local LLM instead?** That's the default — just `ollama pull
> qwen3:latest` (or any model you like). The installer can pull it for you
> during setup, or you can pull it manually later. Embeddings always use
> Ollama regardless.

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

The vault **is** the mind. The model is a cartridge you can swap without
losing anything — your identity, self-model, and goals live in the vault
and are boot-injected every session.

---

## Directives (how to shape its behavior)

VaultBot ships **curious, not opinionated**. It doesn't assume you want
autonomy, or that you hate certain sources, or that you like bullet points.
The `baseline/` folder contains starter directive templates you can copy
into your vault root to set rules:

- `Autonomy-Directive.md` — act on its own, report after the fact
- `Vault-Knowledge-Only-Directive.md` — never reference training data
- `IDK-Fallback-Directive.md` — say "I don't know" when stuck
- `Communication-Preferences.md` — how you like to be talked to (template)

Or just tell VaultBot in chat ("keep your answers short", "don't use
Wikipedia") and it will store that as a directive note itself.

---

## Project structure

```
.
├── .obsidian/plugins/vaultbot/   # The Obsidian plugin (chat UI)
├── vaultbot_stuff/
│   ├── baseline/                  # Starter directive templates (not active)
│   ├── vaultbot_backend/          # The Python backend
│   │   ├── main.py                #   FastAPI server + chat loop
│   │   ├── agent_tools.py         #   tool schemas + system prompt
│   │   ├── self_improver.py       #   safe self-edit + capability audit
│   │   ├── fused_retrieval.py     #   vector + graph + backlink retrieval
│   │   ├── research_engine.py     #   LLM-free web research
│   │   ├── vault_indexer.py       #   FAISS index + chunked embeddings
│   │   ├── vault_graph.py         #   wikilink graph + context builder
│   │   ├── custom_tools/          #   agent-authored tools (grows itself)
│   │   ├── identity/              #   IDENTITY.md, SELF_MODEL.md, GOALS.md
│   │   └── ...
│   ├── System/                    # Architecture docs, procedures, playbooks
│   ├── .env.example                # Template for .env (API keys, model config)
│   ├── CONTRIBUTING.md            # How to contribute
│   ├── README.md                  # This file
│   └── setup.ps1 / setup.sh       # One-click installers
├── README.md                      # GitHub-facing README
├── CONTRIBUTING.md                # GitHub-facing contributing guide
├── SECURITY.md                    # Security policy
└── LICENSE                        # MIT license
```

Your personal content stays at the vault root:
- `User/` — your notes (gitignored)
- `vaultbot_stuff/Memory/` — chat logs (gitignored)
- `vaultbot_stuff/Knowledge/` — research notes (gitignored)
- `vaultbot_stuff/learningMaterial/` — PDFs / textbooks (gitignored)
- `.env` — API keys (gitignored)

## License & contact

**License:** MIT — see [LICENSE](LICENSE). VaultBot is yours to run,
modify, and share.

**Project founder & custodian:** **Sean Kellogg** — project founder and
custodian. Sole merge authority for this repository; final say on project
direction and what ships.

- Email: skellogg124@gmail.com
- Role: maintainer / moderator (no copyright assignment required from
  contributors — see [CONTRIBUTING.md](CONTRIBUTING.md))

**Reporting security issues:** Found a vulnerability? Please report it
privately to skelogg124@gmail.com instead of opening a public issue. See
[SECURITY.md](SECURITY.md) for details.

**Contributing:** See [CONTRIBUTING.md](CONTRIBUTING.md). The short
version: test with `code_run` before `tool_create`, use `safe_write` for
backend edits, and never commit your `.env` or vault contents.