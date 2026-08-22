#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# VaultBot one-click installer for macOS / Linux
#
# Run from any folder:
#   curl -fsSL https://github.com/Ziggibot0/vaultbot/raw/main/setup.sh | bash
# ═══════════════════════════════════════════════════════════════════════════
set -e

FRAMEWORK_NAME="VaultBot"

# ── Install-state resume helpers ──────────────────────────────────────────
# Same resume principle as setup.ps1: write a .vaultbot-install-state.json
# inside the vault folder tracking which steps are done. On re-run, each
# step checks the state and skips if already done. We use grep to check
# (no jq dependency) and append to a temp file + rewrite as JSON to set.
# The state file path is set after step 3 (download) since $VAULT_PATH
# doesn't exist before then. Steps 1-2 are interactive and always run.
STATE_FILE=""

step_done() {
    # Returns 0 (true) if the named step is marked done in the state file.
    [ -z "$STATE_FILE" ] && return 1
    [ ! -f "$STATE_FILE" ] && return 1
    grep -q "\"$1\": true" "$STATE_FILE" 2>/dev/null
}

mark_step_done() {
    # Mark a step as done by rewriting the state JSON with the new key.
    # Uses python3 (already verified as a prerequisite) to parse+write JSON
    # so we don't depend on jq.
    [ -z "$STATE_FILE" ] && return 0
    local step="$1"
    python3 -c "
import json, sys
path = sys.argv[1]
step = sys.argv[2]
try:
    with open(path) as f: state = json.load(f)
except Exception:
    state = {}
state[step] = True
with open(path, 'w') as f: json.dump(state, f)
" "$STATE_FILE" "$step" 2>/dev/null || true
}

set_state_value() {
    # Store an arbitrary string value in the state JSON (used to persist the
    # chat backend + API key across a re-run after a failed step).
    [ -z "$STATE_FILE" ] && return 0
    local key="$1"
    local value="$2"
    python3 -c "
import json, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path) as f: state = json.load(f)
except Exception:
    state = {}
state[key] = value
with open(path, 'w') as f: json.dump(state, f)
" "$STATE_FILE" "$key" "$value" 2>/dev/null || true
}

get_state_value() {
    # Read a string value from the state JSON (empty if absent).
    [ -z "$STATE_FILE" ] && return 0
    [ ! -f "$STATE_FILE" ] && return 0
    local key="$1"
    python3 -c "
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path) as f: state = json.load(f)
except Exception:
    state = {}
print(state.get(key, ''))
" "$STATE_FILE" "$key" 2>/dev/null
}

get_free_cloud_model() {
    # Query OpenRouter's PUBLIC model list (no API key needed) and pick the
    # best free model that can drive the "big" cartridge: free (pricing 0/0)
    # AND >=128K context so it holds the agentic loop + RAG context. Prefer a
    # curated, ordered list of known-good free models; fall back to the first
    # free+capable model the API reports. Prints "" on failure (offline).
    python3 -c "
import json, urllib.request
CURATED = [
    'z-ai/glm-5.2:free',                       # 256K ctx, strong agentic
    'nvidia/nemotron-3-ultra-550b-a55b:free',  # 1M ctx
    'dots-studio/dots-3-note-preview:free',    # 512K ctx
]
try:
    with urllib.request.urlopen('https://openrouter.ai/api/v1/models', timeout=15) as r:
        data = json.load(r)
except Exception:
    print('')
    raise SystemExit(0)
live = set()
first_free = ''
for m in data.get('data', []):
    mid = m.get('id', '')
    if not mid:
        continue
    p = m.get('pricing') or {}
    prompt = str(p.get('prompt', ''))
    completion = str(p.get('completion', ''))
    is_free = prompt in ('0', '0.0') and completion in ('0', '0.0')
    try:
        ctx = int(m.get('context_length') or 0)
    except (TypeError, ValueError):
        ctx = 0
    if is_free and ctx >= 128000:
        live.add(mid)
        if not first_free:
            first_free = mid
for c in CURATED:
    if c in live:
        print(c)
        raise SystemExit(0)
print(first_free)
" 2>/dev/null
}

echo ""
echo "  ============================="
echo "      VaultBot Installer"
echo "  ============================="
echo ""

# ── 1. Prerequisite checks ─────────────────────────────────────────────────
echo ">>> Checking prerequisites..."
missing=()

# Python 3.11+
PY_OK=false
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
    PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
    if [ "$PY_MINOR" -ge 11 ] 2>/dev/null; then
        PY_OK=true
        echo "  [OK] Python: $PY_VERSION"
    fi
fi
if [ "$PY_OK" = false ]; then
    missing+=("Python 3.11+  ->  https://python.org/downloads")
fi

# Ollama
OLLAMA_OK=false
if command -v ollama &>/dev/null; then
    OLLAMA_OK=true
    echo "  [OK] Ollama: $(ollama --version 2>/dev/null || echo 'installed')"
fi
if [ "$OLLAMA_OK" = false ]; then
    missing+=("Ollama  ->  https://ollama.com")
fi

if [ ${#missing[@]} -gt 0 ]; then
    echo ""
    echo "  Almost there! Install these first, then run the command again:"
    echo ""
    for m in "${missing[@]}"; do echo "    - $m"; done
    echo ""
    # Try to open download pages
    for m in "${missing[@]}"; do
        if echo "$m" | grep -q "python.org"; then open "https://python.org/downloads" 2>/dev/null || xdg-open "https://python.org/downloads" 2>/dev/null || true; fi
        if echo "$m" | grep -q "ollama.com";  then open "https://ollama.com" 2>/dev/null || xdg-open "https://ollama.com" 2>/dev/null || true; fi
    done
    exit 1
fi

# ── 2. Ask the user's name ──────────────────────────────────────────────────
echo ""
echo "  What's your name? VaultBot will call you by this."
read -p "  Your name: " OWNER_NAME
OWNER_NAME="${OWNER_NAME:-friend}"
echo ""

# ── 3. Get the repo (git fork, so updates merge cleanly) ───────────────────
# VaultBot installs as a git fork of the upstream repo, NOT a zip snapshot.
# This is what makes two things possible:
#   1. Updates = `git pull upstream main` (a clean merge, not an overwrite).
#   2. Community contributions = the vaultbot's submit_contribution tool
#      pushes fixes to your fork and opens a PR upstream, with zero manual git.
# If `gh` is missing or the user declines auth, installation aborts with a
# clear error. We never fall back to a zip snapshot: a zip has no git
# history, so it can't update itself or share fixes, and we won't let a
# user believe they got a working install when they didn't.
#
# The repo clones into a FRAMEWORK folder ($FRAMEWORK_NAME). Inside it, the
# `myvault/` subfolder is the user's Obsidian vault. The vault folder name
# is fixed ("myvault") so that upstream updates always land in the right
# place. The backend (vaultbot_backend/) and .venv/ live at the framework
# root, OUTSIDE the vault, so the user never sees them.
# Detect if we're already inside a VaultBot repo. This happens when the
# user cloned the repo and ran the installer from inside it. Use the
# existing repo instead of cloning a nested copy.
FRAMEWORK_PATH="$(pwd)/$FRAMEWORK_NAME"
IN_EXISTING_REPO=false

if [ -d "$(pwd)/vaultbot_backend" ] && [ -f "$(pwd)/setup.sh" ]; then
    # Case 1: $PWD itself is a VaultBot repo (installer run from inside it).
    FRAMEWORK_PATH="$(pwd)"
    IN_EXISTING_REPO=true
    echo "  [!]  Already inside a VaultBot repo -- using this folder."
elif [ -d "$FRAMEWORK_PATH" ]; then
    # Case 2: $FRAMEWORK_PATH exists. Verify it's a VaultBot repo before
    # using it; abort if it's an unrelated folder to avoid clobbering.
    if [ -d "$FRAMEWORK_PATH/vaultbot_backend" ] && [ -f "$FRAMEWORK_PATH/setup.sh" ]; then
        IN_EXISTING_REPO=true
        echo "  [!]  Found existing VaultBot repo -- using it."
    else
        echo "  [X]  Folder '$FRAMEWORK_NAME' already exists but isn't a VaultBot repo."
        echo "       Pick a different location or remove the existing folder."
        exit 1
    fi
else
    GH_OK=false
    if command -v gh &>/dev/null; then
        GH_OK=true
    fi

    if [ "$GH_OK" = false ]; then
        # gh CLI is missing. Offer to install it so VaultBot can update itself
        # and share fixes. If the user declines, installation aborts (no zip).
        echo ">>> GitHub CLI not found"
        echo "  VaultBot uses the GitHub CLI ('gh') to update itself and"
        echo "  share fixes with the community. It's optional but recommended."
        echo ""
        printf "  Install GitHub CLI now? (y/n) "
        read -r INSTALL_GH
        if [ "$INSTALL_GH" = "y" ] || [ "$INSTALL_GH" = "yes" ]; then
            echo ">>> Installing GitHub CLI..."
            if command -v brew &>/dev/null; then
                brew install gh >/dev/null 2>&1 && GH_OK=true
            elif command -v apt-get &>/dev/null; then
                sudo apt-get install -y gh >/dev/null 2>&1 && GH_OK=true
            elif command -v dnf &>/dev/null; then
                sudo dnf install -y gh >/dev/null 2>&1 && GH_OK=true
            fi
            if [ "$GH_OK" = true ]; then
                echo "  [OK] GitHub CLI installed"
            else
                echo "  [!]  Could not install GitHub CLI automatically."
                echo "       Install it from https://cli.github.com and re-run."
            fi
        fi
    fi

    if [ "$GH_OK" = true ]; then
        echo ">>> Connecting to GitHub (one-time)..."

        # gh auth status exits 0 when already authenticated. If not, walk the
        # user through the browser login, then VERIFY it actually completed
        # (gh auth login can exit 0 even if the user closed the browser early).
        AUTHD=false
        if gh auth status >/dev/null 2>&1; then
            AUTHD=true
        fi

        if [ "$AUTHD" = false ]; then
            echo ""
            echo "  VaultBot needs a GitHub account so it can update itself"
            echo "  and share fixes with the community."
            echo ""
            echo "  A browser window will open. Sign in to GitHub, then"
            echo "  copy the one-time code back into this window."
            echo ""
            echo "  (No GitHub account? VaultBot requires one to install - it"
            echo "   installs as a fork so it can update itself and share fixes.)"
            echo ""

            RETRY=true
            while [ "$AUTHD" = false ] && [ "$RETRY" = true ]; do
                gh auth login --hostname github.com --git-protocol https --web || true
                if gh auth status >/dev/null 2>&1; then
                    AUTHD=true
                else
                    printf "  Sign-in didn't complete. Try again? (y/n) "
                    read -r AGAIN
                    if [ "$AGAIN" != "y" ] && [ "$AGAIN" != "yes" ]; then
                        RETRY=false
                    fi
                fi
            done

            if [ "$AUTHD" = false ]; then
                GH_OK=false
                echo "  [!]  GitHub sign-in was skipped or didn't complete."
                echo "       VaultBot needs a GitHub account to install (it installs"
                echo "       as a fork so it can update itself and share fixes)."
                echo "       Sign in with:  gh auth login   then re-run this installer."
            else
                echo "  [OK] Signed in to GitHub"
            fi
        fi

        if [ "$GH_OK" = true ]; then
            # Maintainer (has push access) clones directly; everyone else forks.
            HAS_PUSH=false
            PERM=$(gh api repos/Ziggibot0/vaultbot --jq .permissions.push 2>/dev/null || true)
            if [ "$PERM" = "true" ]; then
                HAS_PUSH=true
            fi

            if [ "$HAS_PUSH" = true ]; then
                echo ">>> Cloning VaultBot..."
                gh repo clone Ziggibot0/vaultbot "$FRAMEWORK_NAME"
            else
                echo ">>> Forking VaultBot to your GitHub account..."
                gh repo fork Ziggibot0/vaultbot --clone
                # gh clones to ./vaultbot (lowercase); rename to $FRAMEWORK_NAME
                # if the filesystem is case-sensitive (macOS/Linux).
                if [ -d "vaultbot" ] && [ ! -d "$FRAMEWORK_NAME" ]; then
                    mv "vaultbot" "$FRAMEWORK_NAME"
                fi
            fi

            if [ ! -d "$FRAMEWORK_PATH" ]; then
                echo "  [X]  GitHub clone/fork failed."
                echo "       VaultBot could not be installed. Check your GitHub account"
                echo "       and network connection, then re-run this installer."
                exit 1
            else
                echo "  [OK] VaultBot installed as a git fork (updates will merge cleanly)"
            fi
        fi
    fi

    if [ "$GH_OK" = false ]; then
        echo "  [X]  VaultBot requires a GitHub account to install."
        echo "       VaultBot installs as a git fork so it can update itself and"
        echo "       share fixes. A zip snapshot can't do either, so we don't"
        echo "       install one."
        echo ""
        echo "       To continue:"
        echo "         1. Install the GitHub CLI (https://cli.github.com)"
        echo "         2. Sign in:  gh auth login"
        echo "         3. Re-run this installer."
        exit 1
    fi
fi

# ── 3b. Set the vault path ─────────────────────────────────────────────────
# The repo ships a `myvault/` subfolder (the user's Obsidian vault). The
# folder name is FIXED to "myvault" so that `git pull` updates (new
# procedures, Knowledge notes, plugin code) always merge into the right
# place. Allowing users to rename the vault folder broke updates: a
# renamed vault meant upstream changes to vaultbot-stuff/System/Procedures/
# etc. landed in `vault/` (the old name) while the user's vault lived
# elsewhere, so nobody got procedure updates. Keeping the name fixed
# eliminates that entire class of sync bugs.
VAULT_PATH="$FRAMEWORK_PATH/myvault"
if [ ! -d "$VAULT_PATH" ]; then
    mkdir -p "$VAULT_PATH"
    echo "  [!]  No shipped myvault/ found -- created an empty 'myvault' folder."
fi

# Now that $VAULT_PATH exists, set the install-state file path so steps
# 4-7 can resume if the user re-runs after a partial install.
STATE_FILE="$FRAMEWORK_PATH/.vaultbot-install-state.json"
if [ -f "$STATE_FILE" ]; then
    echo "  [!]  Found previous install state -- resuming where you left off."
fi

# ── 4. Create the Python virtual environment ────────────────────────────────
# `.venv` lives at the FRAMEWORK root (outside the vault), so the user never
# sees it in Obsidian.
VENV_PYTHON="$FRAMEWORK_PATH/.venv/bin/python"
if step_done "venv_created"; then
    echo "  [!]  Virtual environment already created -- skipping."
elif [ -f "$VENV_PYTHON" ]; then
    echo "  [!]  Virtual environment already exists -- skipping."
    mark_step_done "venv_created"
else
    echo ">>> Creating Python environment (a few seconds)..."
    cd "$FRAMEWORK_PATH"
    python3 -m venv .venv
    cd - >/dev/null
    echo "  [OK] Virtual environment created"
    mark_step_done "venv_created"
fi

# ── 5. Install dependencies ─────────────────────────────────────────────────
REQ_PATH="$FRAMEWORK_PATH/vaultbot_backend/requirements.txt"
if step_done "deps_installed"; then
    echo "  [!]  Dependencies already installed -- skipping."
else
    echo ">>> Installing dependencies (5-15 min, one-time only)..."
    echo "  Grab a coffee. This is the longest step."
    "$VENV_PYTHON" -m pip install --upgrade pip --quiet
    "$VENV_PYTHON" -m pip install -r "$REQ_PATH"
    echo "  [OK] Dependencies installed"
    mark_step_done "deps_installed"
fi

# ── 5b. Set up SearXNG search container (optional, needs Docker) ───────────
# SearXNG is a self-hosted meta-search engine that gives VaultBot's research
# feature access to Google, Brave, DuckDuckGo, etc. via one private container.
# Without it, research falls back to keyless backends (DDG Lite, Marginalia,
# arXiv) which are rate-limited and less reliable. With Docker installed, we
# start the container automatically so research works out of the box.
if step_done "searxng_setup"; then
    echo "  [!]  SearXNG search container already set up -- skipping."
else
    DOCKER_OK=false
    if command -v docker &>/dev/null; then
        DOCKER_OK=true
        echo "  [OK] Docker: $(docker --version 2>/dev/null)"
    fi

    if [ "$DOCKER_OK" = true ]; then
        echo ">>> Starting SearXNG search container (one-time, ~30 seconds)..."

        # Check if the container already exists
        EXISTING=$(docker ps -a --filter "name=vaultbot_searxng" --format "{{.Names}}" 2>/dev/null)
        if [ "$EXISTING" = "vaultbot_searxng" ]; then
            echo "  [!]  SearXNG container already exists -- starting it."
            docker start vaultbot_searxng >/dev/null 2>&1
        else
            SETTINGS_PATH="$FRAMEWORK_PATH/vaultbot_backend/searxng_settings.yml"
            if [ -f "$SETTINGS_PATH" ]; then
                docker run -d --name vaultbot_searxng -p 8080:8080 \
                    -v "$SETTINGS_PATH:/etc/searxng/settings.yml:ro" \
                    searxng/searxng 2>&1 | sed 's/^/  /'
            else
                docker run -d --name vaultbot_searxng -p 8080:8080 searxng/searxng 2>&1 | sed 's/^/  /'
            fi
        fi

        # Wait for the container to be ready (up to 30 seconds)
        READY=false
        for i in $(seq 1 15); do
            sleep 2
            if curl -sf -o /dev/null "http://localhost:8080" 2>/dev/null; then
                READY=true
                break
            fi
        done

        if [ "$READY" = true ]; then
            echo "  [OK] SearXNG search container is running on port 8080"
            echo "  VaultBot's research feature can now search Google, Brave, and more."
        else
            echo "  [!]  SearXNG container started but not responding yet."
            echo "  It may need a few more seconds. Research will work once it's ready."
        fi
        mark_step_done "searxng_setup"
    else
        echo "  [!]  Docker not found -- SearXNG search container skipped."
        echo "  Without Docker, VaultBot's research feature uses keyless backends"
        echo "  (DuckDuckGo Lite, Marginalia, arXiv) which work but are rate-limited."
        echo "  Install Docker (https://docker.com) and re-run setup to enable"
        echo "  full web search via SearXNG."
        mark_step_done "searxng_setup"
    fi
fi

# ── 6. Pull embedding + small models via Ollama ────────────────────────────
# The lightweight embedding model (nomic-embed-text, ~270 MB) and the small
# classification model (qwen3.5:4b, ~4 GB) are auto-pulled. The chat/synthesis
# LLM is handled in step 6b based on the user's choice (local vs cloud API).
if step_done "models_pulled"; then
    echo "  [!]  Embedding + small models already downloaded -- skipping."
else
    echo ">>> Downloading embedding model (~270 MB, one-time only)..."
    ollama pull nomic-embed-text
    echo "  [OK] Embedding model ready"
    # The small model (qwen3.5:4b) drives the small cartridge: cheap
    # classification, tagging, and routing. It MUST be >= ~3-4B — a sub-1B
    # model (like the old qwen3.5:0.8b) can't reliably classify or route,
    # which makes VaultBot feel broken. Pull it here so the one-liner is
    # truly all a user needs (no manual `ollama pull` afterward).
    echo ">>> Downloading small model (qwen3.5:4b, ~4 GB) for classification/routing..."
    if ollama pull qwen3.5:4b; then
        echo "  [OK] Small model ready"
    else
        echo "  [!]  Small model pull failed. You can run 'ollama pull qwen3.5:4b' manually later."
    fi
    mark_step_done "models_pulled"
fi

# ── 6b. Ask: local chat model or cloud API? ────────────────────────────────
# The embedding model (above) is mandatory and always local. The CHAT
# model is the user's choice: a local Ollama model (free, private, heavy)
# or a cloud API key (zero local compute, recommended for laptops).
CHAT_BACKEND="ollama"  # default
CHAT_MODEL=""
API_KEY=""
API_BASE_URL=""
API_MODEL=""
if ! step_done "chat_backend_chosen"; then
    echo ""
    echo "  VaultBot needs a chat model to talk to you."
    echo "  Two options:"
    echo "    1. Local (free, private, uses Ollama — already installed)"
    echo "       Downloads a model (1-5 GB). Best if you have 8+ GB RAM."
    echo "    2. Cloud API (zero local compute, recommended for laptops)"
    echo "       Free OpenRouter tier — no credit card needed."
    echo ""
    read -p "  Pick 1 or 2 (default: 1): " CHOICE
    if [ "$CHOICE" = "2" ]; then
        CHAT_BACKEND="openai"
        echo ""
        echo "  VaultBot will use a cloud model (recommended for laptops)."
        echo "  The easiest free option is OpenRouter — it has a free tier"
        echo "  with no credit card required."
        echo ""
        echo "  A browser window will open so you can create an account."
        echo "  Then click 'Create Key', copy it, and paste it back here."
        echo ""
        open "https://openrouter.ai" 2>/dev/null || xdg-open "https://openrouter.ai" 2>/dev/null || true
        open "https://openrouter.ai/keys" 2>/dev/null || xdg-open "https://openrouter.ai/keys" 2>/dev/null || true
        echo "  (If the browser didn't open, go to https://openrouter.ai/keys)"
        echo ""
        read -p "  Paste your API key (or press Enter to skip and add it later): " API_KEY
        if [ -z "$API_KEY" ]; then
            echo ""
            echo "  No problem — you can add your key later. After setup, edit"
            echo "  the .env file and set:"
            echo "    LLM_API_KEY=sk-..."
            echo "    LLM_BASE_URL=https://openrouter.ai/api/v1"
            echo "    LLM_MODEL=z-ai/glm-5.2:free"
            echo "  (Or set LLM_BACKEND=ollama in .env to use local instead.)"
        else
            API_KEY="$(echo "$API_KEY" | tr -d '[:space:]')"
            API_BASE_URL="https://openrouter.ai/api/v1"
            # Pick the best free model that can drive the big cartridge.
            # Live-query OpenRouter's free list so a new user never lands on
            # a deprecated or rate-limited model; fall back to a known-good
            # default if the query fails (offline).
            echo ""
            echo "  Picking a free model for you..."
            API_MODEL="$(get_free_cloud_model)"
            if [ -z "$API_MODEL" ]; then
                API_MODEL="z-ai/glm-5.2:free"
                echo "  [!]  Couldn't reach OpenRouter to pick a model — using a default."
            fi
            echo "  [OK] API key saved — VaultBot will use $API_MODEL (free tier)."
        fi
    else
        CHAT_BACKEND="ollama"
        echo ""
        echo "  Which model? Popular choices:"
        echo "    qwen3:latest       (4-8B, good balance, ~4 GB)"
        echo "    llama3.2:latest    (3B, lightweight, ~2 GB)"
        echo "    qwen3.6:latest      (larger, best quality, ~8 GB)"
        echo "  Type a model name or press Enter for qwen3:latest"
        read -p "  Model name: " CHAT_MODEL
        CHAT_MODEL="${CHAT_MODEL:-qwen3:latest}"
    fi
    # Persist the choice so a re-run after a failed step doesn't reset it.
    set_state_value "chat_backend" "$CHAT_BACKEND"
    set_state_value "chat_model" "$CHAT_MODEL"
    set_state_value "api_key" "$API_KEY"
    set_state_value "api_base_url" "$API_BASE_URL"
    set_state_value "api_model" "$API_MODEL"
    mark_step_done "chat_backend_chosen"
else
    # Resume: restore the previously-chosen backend + key from state.
    CHAT_BACKEND="$(get_state_value "chat_backend")"
    [ -z "$CHAT_BACKEND" ] && CHAT_BACKEND="ollama"
    CHAT_MODEL="$(get_state_value "chat_model")"
    API_KEY="$(get_state_value "api_key")"
    API_BASE_URL="$(get_state_value "api_base_url")"
    API_MODEL="$(get_state_value "api_model")"
fi

# ── 6c. Pull the chat model if local ──────────────────────────────────────
if [ "$CHAT_BACKEND" = "ollama" ] && [ -n "$CHAT_MODEL" ] && ! step_done "chat_model_pulled"; then
    echo ">>> Downloading chat model: $CHAT_MODEL (this can take a while)..."
    echo "  Grab a coffee. Large models take 5-30 min depending on your connection."
    if ollama pull "$CHAT_MODEL"; then
        echo "  [OK] Chat model ready: $CHAT_MODEL"
    else
        echo "  [!]  Chat model pull failed. You can run 'ollama pull $CHAT_MODEL' manually later."
        echo "  VaultBot will still start — you'll just need to pull a model before chatting."
    fi
    mark_step_done "chat_model_pulled"
fi

# ── 7. Write .env with the user's name + LLM config ────────────────────────
ENV_EXAMPLE="$FRAMEWORK_PATH/.env.example"
ENV_FILE="$FRAMEWORK_PATH/.env"
if step_done "env_written"; then
    echo "  [!]  Config already written -- skipping."
elif [ -f "$ENV_EXAMPLE" ]; then
    sed "s/^VAULTBOT_OWNER=.*/VAULTBOT_OWNER=$OWNER_NAME/" "$ENV_EXAMPLE" > "$ENV_FILE"
    sed -i.bak "s|^VAULT_PATH=.*|VAULT_PATH=$VAULT_PATH|" "$ENV_FILE" 2>/dev/null || \
        sed "s|^VAULT_PATH=.*|VAULT_PATH=$VAULT_PATH|" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    sed -i.bak "s/^LLM_BACKEND=.*/LLM_BACKEND=$CHAT_BACKEND/" "$ENV_FILE" 2>/dev/null || \
        sed "s/^LLM_BACKEND=.*/LLM_BACKEND=$CHAT_BACKEND/" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    if [ "$CHAT_BACKEND" = "ollama" ] && [ -n "$CHAT_MODEL" ]; then
        sed -i.bak "s/^OLLAMA_LLM_MODEL=.*/OLLAMA_LLM_MODEL=$CHAT_MODEL/" "$ENV_FILE" 2>/dev/null || \
            sed "s/^OLLAMA_LLM_MODEL=.*/OLLAMA_LLM_MODEL=$CHAT_MODEL/" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    fi
    if [ "$CHAT_BACKEND" = "openai" ] && [ -n "$API_KEY" ]; then
        sed -i.bak "s/^LLM_API_KEY=.*/LLM_API_KEY=$API_KEY/" "$ENV_FILE" 2>/dev/null || \
            sed "s/^LLM_API_KEY=.*/LLM_API_KEY=$API_KEY/" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
        # Use '|' as the sed delimiter: the base URL and model id both contain
        # '/' (https://... and deepseek/...), which would break a s/// command.
        sed -i.bak "s|^LLM_BASE_URL=.*|LLM_BASE_URL=$API_BASE_URL|" "$ENV_FILE" 2>/dev/null || \
            sed "s|^LLM_BASE_URL=.*|LLM_BASE_URL=$API_BASE_URL|" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
        sed -i.bak "s|^LLM_MODEL=.*|LLM_MODEL=$API_MODEL|" "$ENV_FILE" 2>/dev/null || \
            sed "s|^LLM_MODEL=.*|LLM_MODEL=$API_MODEL|" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    fi
    rm -f "${ENV_FILE}.bak" 2>/dev/null
    echo "  [OK] Configured -- VaultBot will call you $OWNER_NAME"
    if [ "$CHAT_BACKEND" = "openai" ] && [ -z "$API_KEY" ]; then
        echo "  Don't forget: add your LLM_API_KEY to .env to use your cloud model."
    fi
    mark_step_done "env_written"
else
    echo "  [!]  .env.example not found -- skipping .env creation"
    mark_step_done "env_written"
fi

# ── 7b. Configure Obsidian (hide repo-hygiene docs) ─────────────────────────
# The repo root carries GitHub-facing docs (AGENTS.md, README.md, SECURITY.md,
# LICENSE, CONTRIBUTING.md) that must stay at the root for GitHub to see them,
# but they should not clutter the user's Obsidian file explorer. Obsidian's
# userIgnoreFilters (in .obsidian/app.json) hides them. We MERGE into any
# existing filters so we never clobber a user's own ignore list.
OBSIDIAN_DIR="$VAULT_PATH/.obsidian"
APP_JSON="$OBSIDIAN_DIR/app.json"
if step_done "obsidian_ignore_configured"; then
    echo "  [!]  Obsidian ignore filters already configured -- skipping."
else
    mkdir -p "$OBSIDIAN_DIR"
    python3 -c "
import json, sys
path = sys.argv[1]
docs = ['AGENTS.md', 'README.md', 'SECURITY.md', 'LICENSE', 'CONTRIBUTING.md']
try:
    with open(path) as f:
        app = json.load(f)
except Exception:
    app = {}
filters = list(app.get('userIgnoreFilters') or [])
for d in docs:
    if d not in filters:
        filters.append(d)
app['userIgnoreFilters'] = filters
with open(path, 'w') as f:
    json.dump(app, f, indent=2)
" "$APP_JSON"
    echo "  [OK] Obsidian configured to hide repo docs from the file explorer"
    mark_step_done "obsidian_ignore_configured"
fi

# ── 7c. Configure Obsidian (dark mode) ──────────────────────────────────────
# Obsidian's appearance.json controls the theme. "baseTheme": "obsidian" is
# the built-in dark theme. We write it so a fresh install opens in dark mode
# without the user having to toggle it manually. We MERGE into any existing
# appearance.json so we never clobber a user's cssTheme or other settings.
APPEARANCE_JSON="$OBSIDIAN_DIR/appearance.json"
if step_done "obsidian_dark_mode"; then
    echo "  [!]  Obsidian dark mode already configured -- skipping."
else
    mkdir -p "$OBSIDIAN_DIR"
    python3 -c "
import json, sys
path = sys.argv[1]
try:
    with open(path) as f:
        appearance = json.load(f)
except Exception:
    appearance = {}
appearance['baseTheme'] = 'obsidian'
with open(path, 'w') as f:
    json.dump(appearance, f, indent=2)
" "$APPEARANCE_JSON"
    echo "  [OK] Obsidian configured to open in dark mode"
    mark_step_done "obsidian_dark_mode"
fi

# ── 8. Done -- open Obsidian ─────────────────────────────────────────────────
echo ""
echo "  ============================="
echo "      Setup Complete!"
echo "  ============================="
echo ""
echo "  Your vault is at:"
echo "    $VAULT_PATH"
echo ""
echo "  Opening Obsidian for you now..."
echo "  (If it doesn't open, open Obsidian manually and"
echo "   choose 'Open folder as vault' -> select the folder above)"
echo ""
echo "  In Obsidian:"
echo "    1. Settings (gear) -> Community plugins"
echo "    2. Turn OFF 'Restricted mode'"
echo "    3. Find VaultBot -> toggle ON"
echo "    4. Click the robot icon in the sidebar"
echo "    5. Say hi!"
echo ""
echo "  VaultBot knows your name is $OWNER_NAME."
echo ""

# Try to open Obsidian deep-linked to the vault
ESCAPED_PATH=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$VAULT_PATH" 2>/dev/null || echo "$VAULT_PATH")
open "obsidian://open?path=$ESCAPED_PATH" 2>/dev/null || xdg-open "obsidian://open?path=$ESCAPED_PATH" 2>/dev/null || true