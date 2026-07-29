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

## TL;DR — the 10-minute install

You need **three free programs** installed first (Python, Ollama, Obsidian).
Then:

1. **Download** this repo (green **Code** button → **Download ZIP**) and unzip
   it somewhere permanent (not in OneDrive/Dropbox).
2. **Double-click `Setup VaultBot.bat`** (Windows) or
   `Setup VaultBot.command` (macOS). A friendly wizard does everything:
   creates the Python environment, installs dependencies, asks your name,
   and pulls the AI models.
3. **Open the folder in Obsidian** → enable the VaultBot plugin → click the
   robot icon → say hi.

That's it. You never need to open a terminal or type a command. The setup
wizard does all the technical work for you, and the Obsidian plugin handles
starting/stopping the backend automatically every day after.

> **Prefer a video?** The wizard's on-screen prompts walk you through each
> step. If anything goes wrong, see [Troubleshooting](#troubleshooting).

---

## Table of contents

1. [What it does](#what-it-does)
2. [Prerequisites (the three free programs)](#prerequisites-the-three-free-programs)
3. [Install in 3 steps (no terminal needed)](#install-in-3-steps-no-terminal-needed)
4. [Day-to-day use](#day-to-day-use)
5. [The setup wizard (what it does for you)](#the-setup-wizard-what-it-does-for-you)
6. [Configuration](#configuration)
7. [Troubleshooting](#troubleshooting)
8. [Updating VaultBot](#updating-vaultbot)
9. [How it thinks](#how-it-thinks)
10. [Directives (how to shape its behavior)](#directives-how-to-shape-its-behavior)
11. [Project structure](#project-structure)
12. [License & contact](#license--contact)

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

## Prerequisites (the three free programs)

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
*Install*. Without this, the setup wizard won't be able to find Python.

**Ollama** — Go to <https://ollama.com>, download the installer, and run it.
It installs a small background service. You'll know it's working when you
see the Ollama icon in your system tray / menu bar.

**Obsidian** — Go to <https://obsidian.md>, download it, and install it.
You don't need to create a vault yet — the install steps below open this
repo *as* a vault.

---

## Install in 3 steps (no terminal needed)

### Step 1 — Get the VaultBot files

1. Go to <https://github.com/ziggibot-uni/vaultbot>.
2. Click the green **Code** button → **Download ZIP**.
3. Unzip the file somewhere permanent (e.g. `C:\Users\yourname\Documents\MyVault`).
   **Don't put it in OneDrive or Dropbox** — syncing services can corrupt the
   database files VaultBot creates.

> **If you have Git installed**, you can instead `git clone
> https://github.com/ziggibot-uni/vaultbot.git MyVault`. Either way works.

### Step 2 — Run the setup wizard

**Windows:** Open the unzipped folder in File Explorer and **double-click
`Setup VaultBot.bat`**.

**macOS:** Open the unzipped folder in Finder and **double-click
`Setup VaultBot.command`**. (The first time, macOS may ask you to confirm
— right-click → **Open** → **Open** to allow it.)

A friendly wizard launches in a new window and does everything for you:

- ✅ Creates the Python virtual environment (no manual activation).
- ✅ Installs all of VaultBot's dependencies (one-time, ~5–15 minutes).
- ✅ Copies `.env.example` → `.env` and asks **"What should VaultBot call you?"**.
- ✅ Detects Ollama and offers to download the AI models (~2 GB, one-time).

You'll see a progress line for each step. When it says **"Setup complete!"**,
close that window and move on to step 3.

> **What if the wizard fails?** The most common cause is Python not being on
> PATH (re-run the Python installer, choose *Modify*, enable "Add Python to
> environment variables"). For anything else see [Troubleshooting](#troubleshooting).

### Step 3 — Open the vault in Obsidian

1. Open Obsidian.
2. Choose **Open folder as vault** and select the folder you unzipped in
   step 1 (the one *containing* `vaultbot_backend`, `.obsidian`, etc. — not
   a subfolder).
3. Go to **Settings** (gear icon, bottom-left) → **Community plugins**.
4. If "Restricted mode" is on, turn it **off** (Obsidian requires this to
   run any community plugin).
5. Find **VaultBot** under *Installed plugins* and toggle it **on**.

### Step 4 — Say hi 👋

A VaultBot icon appears in Obsidian's left sidebar (it looks like a small
robot). Click it to open the chat panel. The plugin automatically starts
the Python backend for you in the background — **you don't need to run any
commands**. The first launch takes ~10–20 seconds while it loads the models
and indexes your vault.

**On the very first launch**, a welcome wizard pops up inside Obsidian and
asks your name (if you didn't enter it during step 2, or want to change it).
Type it and click **Done** — VaultBot will address you by name from then on.

You'll see "VaultBot backend is ready." when it's finished loading. Then
just say hi. It will introduce itself and start learning about you.

---

## Day-to-day use

After the one-time setup, your daily routine is just:

1. **Make sure Ollama is running.** It usually starts automatically on boot
   (check for the Ollama icon in your system tray / menu bar). If not, open
   the Ollama app once to start the service.
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

## The setup wizard (what it does for you)

The `Setup VaultBot` launcher runs `setup_wizard.py`, a self-contained
Python script that performs the exact steps that used to be a long list of
terminal commands. Concretely, it:

| Old manual step (gone) | What the wizard does |
|------------------------|----------------------|
| `python -m venv vaultbot_venv` | Creates the venv for you |
| `vaultbot_venv\Scripts\Activate.ps1` | Never asks you to activate anything |
| `pip install -r vaultbot_backend/requirements.txt` | Installs deps **into** the venv |
| `copy .env.example .env` | Copies the config template |
| Edit `.env` → set `VAULTBOT_OWNER=Your Name` | Asks your name interactively |
| `ollama pull qwen3.6:latest` + `nomic-embed-text` | Offers to pull the models |

It's idempotent: re-running it on an already-set-up vault just confirms
each step is done and lets you update your name. No state is destroyed.

There's also an **in-Obsidian** version: if you skip the `.bat`/`.command`
step (or the venv isn't ready when you first open Obsidian), a welcome
modal pops up offering to launch the one-click setup for you and asks your
name. You can re-open it any time from **Settings → VaultBot → Setup wizard
→ Run setup wizard**.

---

## Configuration

All config lives in `.env` (the wizard creates it for you from
`.env.example`). After editing `.env`, restart the backend (one-click
button in the plugin settings, or close and reopen Obsidian) for changes
to take effect.

| Variable | What it does | Default |
|----------|-------------|---------|
| `VAULTBOT_OWNER` | Your name. VaultBot addresses you by this. Set by the wizard. | (empty — it calls you "the user" until it learns) |
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

**The setup wizard says "Python was not found".**
Python isn't on PATH. Re-run the Python installer from
<https://www.python.org/downloads/>, choose *Modify*, and enable **"Add
Python to environment variables"**. Then re-run `Setup VaultBot.bat` /
`.command`.

**The wizard failed partway through (dependencies).**
Re-run the wizard — it picks up where it left off (the venv already exists,
so it skips straight to installing the remaining packages). If it keeps
failing, check your internet connection; some packages are large.

**"The backend didn't start" / VaultBot isn't responding in chat.**
1. Check Ollama is running (icon in system tray). If not, open the Ollama app.
2. In Obsidian: Settings → Community plugins → VaultBot → gear icon →
   **Restart backend**. Wait ~20 seconds.
3. Still stuck? Run **`Setup VaultBot.bat`** again to verify the venv + deps
   are healthy.
4. As a last resort, look at the last ~20 lines of `vaultbot_backend/backend.log`
   in the vault for an error message.

**macOS says "Setup VaultBot.command cannot be opened because it is from an
unidentified developer."**
Right-click the file → **Open** → click **Open** in the dialog. This is
Gatekeeper being cautious about scripts; the file is safe (it just runs the
`setup_wizard.py` from this repo).

**PowerShell says "running scripts is disabled on this system."**
You only see this if you opened a terminal manually. You don't need to —
use the `Setup VaultBot.bat` launcher instead. If you do want to use a
terminal, run this once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**Model download is slow or fails.** The wizard's `ollama pull` step can be
flaky on slow connections. Re-run the wizard (or just `ollama pull
qwen3.6:latest` yourself) — it resumes where it left off.

**Voice (text-to-speech / speech-to-text) doesn't work.** The voice
stack needs `numpy` 2.x and a working audio device. First launch
downloads the Kokoro + Whisper models automatically (~hundreds of MB) —
give it time. If it still fails, voice is optional; text chat works
without it.

**FAISS / numpy ABI error.** Make sure you installed `faiss-cpu>=1.11`
(the wizard pins it in `requirements.txt`). If you upgraded numpy
separately, re-run the wizard.

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
the ZIP and copy over the `vaultbot_backend/` and
`.obsidian/plugins/vaultbot/` folders. Then restart the backend.

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
│   ├── vault_indexer.py           #   FAISS index + chunked embeddings
│   ├── vault_graph.py             #   wikilink graph + context builder
│   ├── abstract_context.py        #   L2/L1/L0 multi-resolution context
│   ├── embedding_drift.py         #   relevance-feedback drift
│   ├── concept_card.py            #   L1 extractive concept cards
│   ├── moc_builder.py             #   L2 map-of-content clustering
│   ├── identity/                  #   IDENTITY.md, SELF_MODEL.md, GOALS.md
│   ├── custom_tools/              #   agent-authored tools (grows itself)
│   └── ...
├── setup_wizard.py                # One-click setup (runs the wizard logic)
├── Setup VaultBot.bat             # Windows: double-click to launch the wizard
├── Setup VaultBot.command         # macOS: double-click to launch the wizard
├── .env.example                   # Config template (wizard copies to .env)
├── start_backend.bat              # Legacy manual launcher (fallback only)
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