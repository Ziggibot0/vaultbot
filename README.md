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
2. [What you'll need before you start](#what-youll-need-before-you-start)
3. [Step-by-step setup (for first-timers)](#step-by-step-setup-for-first-timers)
4. [Day-to-day use: how to start VaultBot each day](#day-to-day-use-how-to-start-vaultbot-each-day)
5. [Configuration](#configuration)
6. [Troubleshooting](#troubleshooting)
7. [Updating VaultBot](#updating-vaultbot)
8. [How it thinks](#how-it-thinks)
9. [Directives (how to shape its behavior)](#directives-how-to-shape-its-behavior)
10. [Project structure](#project-structure)
11. [License & contact](#license--contact)

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
- **Talks to you** — voice in/out (Kokoro TTS + faster-whisper STT), with
  streaming speech and interrupt.

---

## What you'll need before you start

VaultBot runs **entirely on your own computer** — nothing leaves your
machine unless you choose to add a cloud LLM later. You need three free
programs installed first. Think of them like this:

| Program | What it is | Why VaultBot needs it |
|---------|------------|----------------------|
| **[Python 3.11+](https://www.python.org/downloads/)** | A programming language runtime | Runs VaultBot's "brain" (the backend) |
| **[Ollama](https://ollama.com)** | A local AI model runner | Provides the actual language model that does the thinking |
| **[Obsidian](https://obsidian.md)** | A note-taking app | This *is* the vault — where your notes and VaultBot's memory live |

> **Optional:** [Docker](https://www.docker.com) — only if you want to run
> the self-hosted SearXNG search backend. Skip it for a first install.

### Installing the three prerequisites

**Python** — Go to <https://www.python.org/downloads/>, download the latest
Python 3 installer, and run it. **Important:** on the first screen of the
installer, check the box that says **"Add Python to PATH"** before clicking
*Install*. Without this, the setup commands below won't find Python.

**Ollama** — Go to <https://ollama.com>, download the installer, and run it.
It installs a small background service. You'll know it's working when you
see the Ollama icon in your system tray / menu bar.

**Obsidian** — Go to <https://obsidian.md>, download it, and install it.
You don't need to create a vault yet — we'll do that in the setup steps
below by opening this repo *as* a vault.

---

## Step-by-step setup (for first-timers)

This is a one-time install. Once it's done, day-to-day use is just "open
Obsidian and start chatting" (see [Day-to-day use](#day-to-day-use-how-to-start-vaultbot-each-day)).

### 1. Get the VaultBot files

**Option A — you have Git installed:**

```bash
git clone https://github.com/ziggibot-uni/vaultbot.git MyVault
cd MyVault
```

**Option B — you don't have Git (simplest for most people):**

1. Go to <https://github.com/ziggibot-uni/vaultbot>.
2. Click the green **Code** button → **Download ZIP**.
3. Unzip the file somewhere permanent (e.g. `C:\Users\yourname\Documents\MyVault`).
   Don't put it in OneDrive or Dropbox — syncing services can corrupt the
   database files VaultBot creates.
4. Open a terminal in that folder (see below for how).

> **Tip — opening a terminal in the right folder (Windows):** Open the
> folder in File Explorer, click the address bar at the top, type `powershell`,
> and press Enter. A blue terminal opens already inside the folder. On
> macOS, open Terminal and `cd` to the folder.

### 2. Create a Python virtual environment

A virtual environment is a self-contained copy of Python just for
VaultBot, so it doesn't interfere with anything else on your computer.
**It must be named `vaultbot_venv`** — the Obsidian plugin looks for this
exact folder to start the backend automatically.

```bash
python -m venv vaultbot_venv
```

This takes a few seconds and creates a folder called `vaultbot_venv`.

### 3. Activate the virtual environment

Activate it so the next commands run inside it:

```bash
# Windows (PowerShell):
vaultbot_venv\Scripts\Activate.ps1

# Windows (Command Prompt / the .bat launcher):
vaultbot_venv\Scripts\activate.bat

# macOS / Linux:
source vaultbot_venv/bin/activate
```

You'll know it worked when your terminal prompt shows `(vaultbot_venv)`
at the start of the line.

> **Windows PowerShell error about "running scripts is disabled"?**
> Run this once, then try again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

### 4. Install VaultBot's dependencies

```bash
pip install -r vaultbot_backend/requirements.txt
```

This downloads and installs all the Python packages VaultBot needs. It
can take 5–15 minutes the first time (some packages are large, including
the voice and PDF-OCR stacks). You only do this once.

### 5. Download the AI models (one-time, ~2 GB)

Ollama needs the actual "brain" models. Open a **new** terminal (or just
run these in any terminal — Ollama is separate from Python) and run:

```bash
ollama pull qwen3.6:latest
ollama pull nomic-embed-text
```

- `qwen3.6:latest` — the main language model that does the thinking (the
  default; you can use any model you prefer).
- `nomic-embed-text` — a small (~270 MB) model that turns your notes into
  searchable vectors so VaultBot can find them by meaning, not keywords.

The downloads are large and can take a while depending on your internet.
You only do this once.

### 6. Configure your settings

```bash
# Windows copy:
copy .env.example .env

# macOS / Linux:
cp .env.example .env
```

Now open the new `.env` file in a text editor (Notepad, VS Code, or even
Obsidian's own file browser). The only setting most people need to change
is:

```
VAULTBOT_OWNER=Your Name
```

Replace `Your Name` with what you want VaultBot to call you. Leave
everything else at the defaults for now — VaultBot works out of the box
with local Ollama and keyless web search. See [Configuration](#configuration)
for the optional settings.

> **Don't delete `.env.example`** — it's the template. And never commit
> your `.env` to Git if you push your vault online; it may hold API keys.

### 7. Open the vault in Obsidian

1. Open Obsidian.
2. If it asks, choose **Open folder as vault** and select the `MyVault`
   folder you created in step 1 (the one *containing* `vaultbot_backend`,
   `.obsidian`, etc. — not a subfolder).
3. Go to **Settings** (gear icon, bottom-left) → **Community plugins**.
4. If "Restricted mode" is on, turn it **off** (Obsidian requires this to
   run any community plugin).
5. You should see **VaultBot** listed under *Installed plugins*. Toggle it
   **on**. (It lives in `.obsidian/plugins/vaultbot/`.)

### 8. Start chatting

A VaultBot icon will appear in Obsidian's left sidebar (it looks like a
small robot). Click it to open the chat panel. The plugin automatically
starts the Python backend for you in the background — you don't need to
run any commands. The first launch takes ~10–20 seconds while it loads the
models and indexes your vault.

You'll see a notice "VaultBot backend is ready." when it's done. Then
just say hi. It will introduce itself and start learning about you.

> **If the backend doesn't start automatically** (rare, usually a venv
> path issue), you can start it manually as a fallback: double-click
> `start_backend.bat` in the vault root (Windows), or run
> `python vaultbot_backend/main.py` in an activated terminal. Then reload
> the Obsidian plugin (disable → re-enable).

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
| `OLLAMA_LLM_MODEL` | The local LLM for synthesis | `qwen3.6:latest` |
| `OLLAMA_EMBED_MODEL` | The embedding model | `nomic-embed-text` |
| `LLM_BACKEND` | `ollama` (local, free) or `openai` (cloud, any OpenAI-compatible API) | `ollama` |
| `LLM_API_KEY` | Cloud API key (leave blank for local-only) | (empty) |
| `LLM_BASE_URL` | Cloud API base URL (OpenAI, OpenRouter, LM Studio, vLLM, etc.) | `https://api.openai.com` |
| `LLM_MODEL` | Cloud model name (only used when `LLM_BACKEND=openai`) | `gpt-4o-mini` |
| `VAULTBOT_RESEARCH_BACKEND` | `freesearch` (keyless) or `tavily` (API key) | `freesearch` |
| `TAVILY_API_KEY` | Tavily search API key (only if using `tavily`) | (empty) |
| `SEARXNG_PORT` | Port for the optional self-hosted SearXNG container | `8080` |

> **Want to use a cloud model (like GPT-4o) instead of local?** Set
> `LLM_BACKEND=openai`, `LLM_API_KEY=your-key`, and `LLM_MODEL=gpt-4o-mini`
> (or any OpenAI/OpenRouter model). Embeddings stay local on Ollama either
> way. You can also switch back and forth live from the plugin settings
> panel without editing `.env` — the changes persist for you.

---

## Troubleshooting

**"The backend didn't start" / VaultBot isn't responding in chat.**
1. Check Ollama is running (icon in system tray). If not, open the Ollama app.
2. In Obsidian: Settings → Community plugins → VaultBot → gear icon →
   **Restart backend**. Wait ~20 seconds.
3. Still stuck? Open `vaultbot_backend/backend.log` in the vault and look
   at the last ~20 lines for an error message.
4. As a last resort, open a terminal in the vault folder, activate the
   venv (`vaultbot_venv\Scripts\Activate.ps1`), and run
   `python vaultbot_backend/main.py` — any error will print directly.

**PowerShell says "running scripts is disabled on this system."**
Run this once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**`pip` or `python` not found.** Python wasn't added to PATH during
install. Re-run the Python installer, choose *Modify*, and enable
"Add Python to environment variables".

**Model download is slow or fails.** `ollama pull` can be flaky on slow
connections. Just re-run the same `ollama pull` command — it resumes where
it left off.

**Voice (text-to-speech / speech-to-text) doesn't work.** The voice
stack needs `numpy` 2.x and a working audio device. First launch
downloads the Kokoro + Whisper models automatically (~hundreds of MB) —
give it time. If it still fails, voice is optional; text chat works
without it.

**FAISS / numpy ABI error.** Make sure you installed `faiss-cpu>=1.11`
(it's pinned in `requirements.txt`). If you upgraded numpy separately,
reinstall: `pip install --force-reinstall faiss-cpu>=1.11`.

**Port 8000 already in use.** Something else on your machine is using the
backend's port. Close other Python servers, or change the port in
`vaultbot_backend/main.py` (advanced).

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
├── .obsidian/plugins/vaultbot/   # The Obsidian plugin (chat UI, voice, TTS)
├── baseline/                      # Starter directive templates (not active)
├── learningMaterial/              # Your PDFs / textbooks (gitignored)
├── vaultbot/                      # The vault: your notes (gitignored)
│   ├── chat/                      #   conversation logs
│   ├── research/                  #   autonomous research notes
│   └── textbooks/                 #   ingested textbook sections
├── vaultbot_backend/              # The Python backend
│   ├── main.py                    #   FastAPI server + chat loop
│   ├── agent_tools.py             #   tool schemas + system prompt
│   ├── self_improver.py           #   safe self-edit + capability audit
│   ├── fused_retrieval.py         #   vector + graph + backlink retrieval
│   ├── research_engine.py         #   LLM-free web research
│   ├── vault_indexer.py            #   FAISS index + chunked embeddings
│   ├── vault_graph.py             #   wikilink graph + context builder
│   ├── abstract_context.py        #   L2/L1/L0 multi-resolution context
│   ├── embedding_drift.py         #   relevance-feedback drift
│   ├── concept_card.py            #   L1 extractive concept cards
│   ├── moc_builder.py             #   L2 map-of-content clustering
│   ├── identity/                  #   IDENTITY.md, SELF_MODEL.md, GOALS.md
│   ├── custom_tools/              #   agent-authored tools (grows itself)
│   └── ...
├── .env.example                   # Copy to .env and configure
├── start_backend.bat              # Windows launcher (fallback only)
└── vaultbot_backend/requirements.txt
```

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