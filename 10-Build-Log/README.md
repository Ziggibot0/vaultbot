# VaultBot

> A self-improving AI research agent that lives inside your Obsidian vault.
> It thinks with your notes, researches from the web, writes permanent
> knowledge, and grows itself — all while spending minimal LLM calls.

VaultBot is not a chatbot. It's a **personal intelligence system** that
treats your Obsidian vault as its mind. The LLM is swappable plumbing; the
vault — your notes, your links, your shared history — is what it actually
knows. It researches gaps, writes sourced notes, builds its own tools, and
gets smarter the more you use it.

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

## Quick start

### Prerequisites

- [Python 3.11+](https://python.org) — check "Add Python to PATH" during install
- [Ollama](https://ollama.com) — the local LLM + embedding engine
- [Obsidian](https://obsidian.md) — your vault is VaultBot's mind
- (Optional) [Docker](https://docker.com) — for self-hosted SearXNG search

### One-command setup

```powershell
# Windows (PowerShell)
irm https://github.com/ziggibot-uni/vaultbot/raw/main/setup.ps1 | iex
```
```bash
# macOS / Linux
curl -fsSL https://github.com/ziggibot-uni/vaultbot/raw/main/setup.sh | bash
```

The installer asks your name, downloads everything, and opens Obsidian
for you. After it finishes, enable VaultBot in Settings → Community
plugins → say hi.

## Configuration

All config lives in `.env` (copy from `.env.example`):

| Variable | What it does | Default |
|----------|-------------|---------|
| `VAULTBOT_OWNER` | Your name. VaultBot addresses you by this. | (empty — it calls you "the user" until it learns) |
| `OLLAMA_LLM_MODEL` | Local LLM for synthesis (only used when `LLM_BACKEND=ollama`; must `ollama pull` manually) | `qwen3.6:latest` |
| `OLLAMA_EMBED_MODEL` | Embedding model (auto-pulled by installer, ~270 MB) | `nomic-embed-text` |
| `LLM_BACKEND` | `ollama` (local, needs manual model pull) or `openai` (cloud, **recommended for laptops**) | `openai` |
| `LLM_API_KEY` | Cloud API key (leave blank for local-only) | (empty) |
| `VAULTBOT_RESEARCH_BACKEND` | `freesearch` (keyless) or `tavily` (API key) | `freesearch` |

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
├── .env.example                   # Template — installer copies to .env
├── setup.ps1                      # One-click installer (Windows)
├── setup.sh                       # One-click installer (macOS/Linux)
└── requirements.txt
```

## License

MIT — see [LICENSE](LICENSE). VaultBot is yours to run, modify, and share.

## Project founder & custodian

**Sean Kellogg** — project founder and custodian. Sole merge authority for
this repository; final say on project direction and what ships.

- Email: skelogg124@gmail.com
- Role: maintainer / moderator (no copyright assignment required from
  contributors — see [CONTRIBUTING.md](CONTRIBUTING.md))

## Reporting security issues

Found a vulnerability? Please report it privately to
skelogg124@gmail.com instead of opening a public issue. See
[SECURITY.md](SECURITY.md) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: test with
`code_run` before `tool_create`, use `safe_write` for backend edits, and
never commit your `.env` or vault contents.