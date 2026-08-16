#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# VaultBot one-click installer for macOS / Linux
#
# Run from any folder:
#   curl -fsSL https://github.com/ziggibot-uni/vaultbot/raw/main/vaultbot/setup.sh | bash
# ═══════════════════════════════════════════════════════════════════════════
set -e

REPO_ZIP="https://github.com/ziggibot-uni/vaultbot/archive/refs/heads/main.zip"
VAULT_NAME="VaultBot"

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

# ── 3. Download the repo ────────────────────────────────────────────────────
VAULT_PATH="$(pwd)/$VAULT_NAME"
if [ -d "$VAULT_PATH" ]; then
    echo "  [!]  Folder '$VAULT_NAME' already exists -- using it."
else
    echo ">>> Downloading VaultBot..."
    TMP_ZIP="/tmp/vaultbot-setup-$$.zip"
    TMP_EXTRACT="/tmp/vaultbot-extract-$$"
    curl -fsSL -o "$TMP_ZIP" "$REPO_ZIP"
    echo ">>> Extracting..."
    mkdir -p "$TMP_EXTRACT"
    # macOS tar auto-detects zip; Linux tar needs -xzf
    if [[ "$(uname)" == "Darwin" ]]; then
        tar -xf "$TMP_ZIP" -C "$TMP_EXTRACT"
    else
        tar -xzf "$TMP_ZIP" -C "$TMP_EXTRACT"
    fi
    INNER=$(ls -d "$TMP_EXTRACT"/*/ | head -1)
    mv "$INNER" "$VAULT_PATH"
    rm -f "$TMP_ZIP"
    rm -rf "$TMP_EXTRACT"
    echo "  [OK] Downloaded to $VAULT_PATH"
fi

# Now that $VAULT_PATH exists, set the install-state file path so steps
# 4-7 can resume if the user re-runs after a partial install.
STATE_FILE="$VAULT_PATH/.vaultbot-install-state.json"
if [ -f "$STATE_FILE" ]; then
    echo "  [!]  Found previous install state -- resuming where you left off."
fi

# ── 4. Create the Python virtual environment ────────────────────────────────
# `.venv` is hidden in the Obsidian file explorer (dots are filtered),
# keeping the vault clean for end users.
VENV_PYTHON="$VAULT_PATH/.venv/bin/python"
if step_done "venv_created"; then
    echo "  [!]  Virtual environment already created -- skipping."
elif [ -f "$VENV_PYTHON" ]; then
    echo "  [!]  Virtual environment already exists -- skipping."
    mark_step_done "venv_created"
else
    echo ">>> Creating Python environment (a few seconds)..."
    cd "$VAULT_PATH"
    python3 -m venv .venv
    cd - >/dev/null
    echo "  [OK] Virtual environment created"
    mark_step_done "venv_created"
fi

# ── 5. Install dependencies ─────────────────────────────────────────────────
REQ_PATH="$VAULT_PATH/vaultbot/vaultbot_backend/requirements.txt"
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

# ── 6. Pull embedding model via Ollama ────────────────────────────────────
# Only the lightweight embedding model (nomic-embed-text, ~270 MB) is
# auto-pulled. The chat/synthesis LLM is handled in step 6b based on the
# user's choice (local Ollama vs cloud API).
if step_done "models_pulled"; then
    echo "  [!]  Embedding model already downloaded -- skipping."
else
    echo ">>> Downloading embedding model (~270 MB, one-time only)..."
    ollama pull nomic-embed-text
    echo "  [OK] Embedding model ready"
    mark_step_done "models_pulled"
fi

# ── 6b. Ask: local chat model or cloud API? ────────────────────────────────
# The embedding model (above) is mandatory and always local. The CHAT
# model is the user's choice: a local Ollama model (free, private, heavy)
# or a cloud API key (zero local compute, recommended for laptops).
CHAT_BACKEND="ollama"  # default
CHAT_MODEL=""
if ! step_done "chat_backend_chosen"; then
    echo ""
    echo "  VaultBot needs a chat model to talk to you."
    echo "  Two options:"
    echo "    1. Local (free, private, uses Ollama — already installed)"
    echo "       Downloads a model (1-5 GB). Best if you have 8+ GB RAM."
    echo "    2. Cloud API (zero local compute, recommended for laptops)"
    echo "       You provide an API key later (OpenAI, OpenRouter, etc.)."
    echo ""
    read -p "  Pick 1 or 2 (default: 1): " CHOICE
    if [ "$CHOICE" = "2" ]; then
        CHAT_BACKEND="openai"
        echo ""
        echo "  You'll need an API key from OpenAI, OpenRouter, or any"
        echo "  OpenAI-compatible provider. Add it to .env after setup:"
        echo "    LLM_API_KEY=sk-..."
        echo "    LLM_MODEL=gpt-4o-mini"
        echo "  (Or set LLM_BACKEND=ollama in .env to use local instead.)"
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
    mark_step_done "chat_backend_chosen"
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
ENV_EXAMPLE="$VAULT_PATH/vaultbot/.env.example"
ENV_FILE="$VAULT_PATH/.env"
if step_done "env_written"; then
    echo "  [!]  Config already written -- skipping."
elif [ -f "$ENV_EXAMPLE" ]; then
    sed "s/^VAULTBOT_OWNER=.*/VAULTBOT_OWNER=$OWNER_NAME/" "$ENV_EXAMPLE" > "$ENV_FILE"
    sed -i.bak "s/^LLM_BACKEND=.*/LLM_BACKEND=$CHAT_BACKEND/" "$ENV_FILE" 2>/dev/null || \
        sed "s/^LLM_BACKEND=.*/LLM_BACKEND=$CHAT_BACKEND/" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    if [ "$CHAT_BACKEND" = "ollama" ] && [ -n "$CHAT_MODEL" ]; then
        sed -i.bak "s/^OLLAMA_LLM_MODEL=.*/OLLAMA_LLM_MODEL=$CHAT_MODEL/" "$ENV_FILE" 2>/dev/null || \
            sed "s/^OLLAMA_LLM_MODEL=.*/OLLAMA_LLM_MODEL=$CHAT_MODEL/" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    fi
    rm -f "${ENV_FILE}.bak" 2>/dev/null
    echo "  [OK] Configured -- VaultBot will call you $OWNER_NAME"
    if [ "$CHAT_BACKEND" = "openai" ]; then
        echo "  Don't forget: add your LLM_API_KEY to .env to use your cloud model."
    fi
    mark_step_done "env_written"
else
    echo "  [!]  vaultbot/.env.example not found -- skipping .env creation"
    mark_step_done "env_written"
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