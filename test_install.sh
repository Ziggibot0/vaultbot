#!/usr/bin/env bash
# ============================================================
# VaultBot Fresh Install Test
# Simulates what a new user experiences when running setup.sh
# Skips interactive prompts and Ollama model pulls
# ============================================================
set -e

REPO_ZIP="https://github.com/Ziggibot0/vaultbot/archive/refs/heads/main.zip"
VAULT_NAME="VaultBot"
VAULT_PATH="/home/vaultbotuser/$VAULT_NAME"
PASS=0
FAIL=0

ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
section() { echo ""; echo "=== $1 ==="; }

section "1. Download repo (simulating fresh user)"
TMP_ZIP="/tmp/vaultbot-setup-test.zip"
TMP_EXTRACT="/tmp/vaultbot-extract-test"

curl -fsSL -o "$TMP_ZIP" "$REPO_ZIP"
mkdir -p "$TMP_EXTRACT"
unzip -q "$TMP_ZIP" -d "$TMP_EXTRACT"
INNER=$(ls -d "$TMP_EXTRACT"/*/ | head -1)
mv "$INNER" "$VAULT_PATH"
rm -f "$TMP_ZIP"
rm -rf "$TMP_EXTRACT"

if [ -d "$VAULT_PATH" ]; then
    ok "Repo downloaded to $VAULT_PATH"
else
    fail "Repo download failed"
    exit 1
fi

section "2. Verify file structure"
# Check that key files exist where the setup script expects them
check_file() {
    if [ -f "$VAULT_PATH/$1" ]; then ok "$1 exists"
    else fail "$1 missing"; fi
}
check_file "setup.sh"
check_file "setup.ps1"
check_file "README.md"
check_file "CONTRIBUTING.md"
check_file "SECURITY.md"
check_file "LICENSE"
check_file ".env.example"
check_file "vaultbot_backend/requirements.txt"
check_file "vaultbot_backend/main.py"
check_file "myvault/vaultbot-stuff/System/Procedures/Dev-Cycle.md"
check_file "myvault/.obsidian/plugins/vaultbot/main.js"
check_file "myvault/.obsidian/plugins/vaultbot/manifest.json"

# Check .gitignore exists and has key entries
if [ -f "$VAULT_PATH/.gitignore" ]; then
    if grep -q "^\.env$" "$VAULT_PATH/.gitignore"; then ok ".gitignore has .env"
    else fail ".gitignore missing .env entry"; fi
else
    fail ".gitignore missing"
fi

section "3. Create venv and install deps (non-interactive)"
VENV_PYTHON="$VAULT_PATH/.venv/bin/python"

cd "$VAULT_PATH"
python3 -m venv .venv
if [ -f "$VENV_PYTHON" ]; then ok "venv created"
else fail "venv creation failed"; fi

echo "  Installing dependencies (this takes a few minutes)..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet 2>&1 | tail -1
"$VENV_PYTHON" -m pip install -r "$VAULT_PATH/vaultbot_backend/requirements.txt" 2>&1 | tail -3

# Verify key deps importable
for mod in fastapi uvicorn requests bs4 faiss watchdog numpy dotenv; do
    if "$VENV_PYTHON" -c "import $mod" 2>/dev/null; then ok "import $mod"
    else fail "import $mod"; fi
done

section "4. Write .env (non-interactive)"
ENV_EXAMPLE="$VAULT_PATH/.env.example"
ENV_FILE="$VAULT_PATH/.env"

if [ -f "$ENV_EXAMPLE" ]; then
    sed "s/^VAULTBOT_OWNER=.*/VAULTBOT_OWNER=TestUser/" "$ENV_EXAMPLE" > "$ENV_FILE"
    sed -i "s/^LLM_BACKEND=.*/LLM_BACKEND=ollama/" "$ENV_FILE"
    sed -i "s/^OLLAMA_LLM_MODEL=.*/OLLAMA_LLM_MODEL=qwen3:latest/" "$ENV_FILE"
    sed -i "s|^VAULT_PATH=.*|VAULT_PATH=$VAULT_PATH|" "$ENV_FILE"
    ok ".env written"
else
    fail ".env.example not found"
fi

if [ -f "$ENV_FILE" ] && grep -q "VAULTBOT_OWNER=TestUser" "$ENV_FILE"; then
    ok ".env has correct owner name"
else
    fail ".env not written correctly"
fi

section "5. Backend import test"
# Create directories the backend expects to exist
mkdir -p "$VAULT_PATH/myvault/vaultbot-stuff/Memory/Chat"
mkdir -p "$VAULT_PATH/myvault/vaultbot-stuff/Memory/Build-Log"
mkdir -p "$VAULT_PATH/myvault/vaultbot-stuff/Knowledge/Research"
cd "$VAULT_PATH"
VAULT_PATH="$VAULT_PATH" "$VENV_PYTHON" -c "
import sys, os
sys.path.insert(0, '$VAULT_PATH/vaultbot_backend')
try:
    import main
    print('  [PASS] main.py imports successfully')
except Exception as e:
    print(f'  [FAIL] main.py import failed: {e}')
    sys.exit(1)
" 2>&1 || fail "backend import failed"

if [ $? -eq 0 ]; then ok "backend imports successfully"; fi

section "6. Verify .gitignore protects sensitive files"
cd "$VAULT_PATH"
# Simulate: create a fake .env with a fake token (overwrite the real one temporarily)
cp .env .env.test-backup
echo "GITHUB_TOKEN=ghp_fake_token_for_testing" > .env
# Create fake sensitive dirs
mkdir -p myvault/vaultbot-stuff/Memory/Chat
mkdir -p vaultbot_backend/sessions
echo "fake session" > vaultbot_backend/sessions/test.jsonl

# Init git and check .env is NOT tracked
git init -q 2>/dev/null
if git check-ignore .env >/dev/null 2>&1; then
    ok ".env is gitignored"
else
    fail ".env would be tracked by git"
fi
# Restore .env
mv .env.test-backup .env

if git add -A --dry-run 2>/dev/null | grep -q "sessions/"; then
    fail "sessions/ would be tracked by git"
else
    ok "sessions/ is gitignored"
fi

if git add -A --dry-run 2>/dev/null | grep -q "Memory/"; then
    fail "Memory/ would be tracked by git"
else
    ok "Memory/ is gitignored"
fi

section "7. Custom tools check"
TOOLS_DIR="$VAULT_PATH/vaultbot_backend/custom_tools"
if [ -d "$TOOLS_DIR" ]; then
    TOOL_COUNT=$(ls "$TOOLS_DIR"/*.py 2>/dev/null | wc -l)
    if [ "$TOOL_COUNT" -ge 3 ]; then ok "custom_tools/ has $TOOL_COUNT tools"
    else fail "custom_tools/ has only $TOOL_COUNT tools (expected 3+)"; fi
    
    # Check key tools exist
    for tool in submit_contribution review_contributions torture_test; do
        if [ -f "$TOOLS_DIR/${tool}.py" ]; then ok "$tool.py exists"
        else fail "$tool.py missing"; fi
    done
else
    fail "custom_tools/ directory missing"
fi

section "RESULTS"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "  STATUS: FAILED"
    exit 1
else
    echo "  STATUS: ALL TESTS PASSED"
fi
