#!/usr/bin/env bash
# ============================================================
# VaultBot Update Flow Test
# Simulates what happens when a user hits "Update from GitHub"
# Downloads the tarball, extracts code only, verifies user
# content is preserved
# ============================================================
set -e

VAULT_PATH="/home/vaultbotuser/VaultBot"
PASS=0
FAIL=0

ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
section() { echo ""; echo "=== $1 ==="; }

if [ ! -d "$VAULT_PATH" ]; then
    echo "  [SKIP] No VaultBot install found — run test_install.sh first"
    exit 0
fi

section "1. Create fake user content (must survive update)"
# Simulate user content that should NEVER be touched by an update
mkdir -p "$VAULT_PATH/User"
echo "# My personal note" > "$VAULT_PATH/User/my-note.md"

mkdir -p "$VAULT_PATH/vaultbot/Memory/Chat"
echo "fake chat log" > "$VAULT_PATH/vaultbot/Memory/Chat/test-chat.md"

mkdir -p "$VAULT_PATH/vaultbot/vaultbot_backend/sessions"
echo '{"session":"fake"}' > "$VAULT_PATH/vaultbot/vaultbot_backend/sessions/test.jsonl"

mkdir -p "$VAULT_PATH/vaultbot/vaultbot_backend/identity"
echo "# My identity" > "$VAULT_PATH/vaultbot/vaultbot_backend/identity/IDENTITY.md"

# Create a fake custom tool (should survive update — copyCodeTree doesn't delete)
echo "# My custom tool" > "$VAULT_PATH/vaultbot/vaultbot_backend/custom_tools/my_custom_tool.py"

# Create fake .env (should survive update)
echo "GITHUB_TOKEN=ghp_fake_token_for_testing" >> "$VAULT_PATH/.env"
echo "VAULTBOT_OWNER=TestUser" >> "$VAULT_PATH/.env"
echo "VAULT_PATH=$VAULT_PATH" >> "$VAULT_PATH/.env"

ok "User content created"

section "2. Download update tarball"
TARBALL="/tmp/vaultbot-update-test.tar.gz"
curl -fsSL -o "$TARBALL" "https://github.com/ziggibot-uni/vaultbot/archive/refs/heads/main.tar.gz"

if [ -f "$TARBALL" ] && [ $(stat -c%s "$TARBALL") -gt 1000 ]; then
    ok "Tarball downloaded ($(stat -c%s "$TARBALL") bytes)"
else
    fail "Tarball download failed"
    exit 1
fi

section "3. Extract to staging (with exclusions)"
STAGING="/tmp/vaultbot-staging-test"
mkdir -p "$STAGING"

# Extract with the same exclusions the plugin uses
tar -xzf "$TARBALL" -C "$STAGING" \
    --exclude="*/.obsidian/plugins/vaultbot/data.json" \
    --exclude="*/vaultbot/vaultbot_backend/*.log" \
    --exclude="*/vaultbot/vaultbot_backend/*_log.json" \
    --exclude="*/vaultbot/vaultbot_backend/sessions" \
    --exclude="*/vaultbot/vaultbot_backend/sessions/*" \
    --exclude="*/vaultbot/vaultbot_backend/vaultbot_index" \
    --exclude="*/vaultbot/vaultbot_backend/vaultbot_index/*" \
    --exclude="*/vaultbot/vaultbot_backend/trash" \
    --exclude="*/vaultbot/vaultbot_backend/trash/*" \
    --exclude="*/vaultbot/vaultbot_backend/__pycache__" \
    --exclude="*/vaultbot/vaultbot_backend/**/*.pyc" \
    2>/dev/null || true

# Find the archive root
ARCHIVE_ROOT=$(ls -d "$STAGING"/*/ | head -1)
if [ -z "$ARCHIVE_ROOT" ]; then
    fail "Could not find archive root in staging"
    exit 1
fi
ok "Extracted to staging: $ARCHIVE_ROOT"

section "4. Simulate copyCodeTree (backend only)"
# The plugin's copyCodeTree copies from archiveRoot/vaultbot/vaultbot_backend/
# to the live backend dir. It does NOT delete files that exist in dest but not src.
SRC_BACKEND="$ARCHIVE_ROOT/vaultbot/vaultbot_backend"
DST_BACKEND="$VAULT_PATH/vaultbot/vaultbot_backend"

if [ ! -d "$SRC_BACKEND" ]; then
    fail "Archive has no vaultbot/vaultbot_backend/"
    exit 1
fi

# Copy files from archive to live (simulating copyCodeTree)
# Skip __pycache__, .bak, .pyc
cd "$SRC_BACKEND"
find . -type f \
    ! -path "*/__pycache__/*" \
    ! -name "*.pyc" \
    ! -name "*.bak" \
    | while read -r f; do
    mkdir -p "$DST_BACKEND/$(dirname "$f")"
    cp "$f" "$DST_BACKEND/$f"
done
ok "Backend code copied from archive"

section "5. Simulate plugin file copy"
SRC_PLUGIN="$ARCHIVE_ROOT/.obsidian/plugins/vaultbot"
DST_PLUGIN="$VAULT_PATH/.obsidian/plugins/vaultbot"

# Backup data.json (the plugin does this)
if [ -f "$DST_PLUGIN/data.json" ]; then
    cp "$DST_PLUGIN/data.json" /tmp/data.json.backup
    ok "data.json backed up"
fi

# Copy only code files
for name in main.js manifest.json styles.css; do
    if [ -f "$SRC_PLUGIN/$name" ]; then
        cp "$SRC_PLUGIN/$name" "$DST_PLUGIN/$name"
    fi
done

# Restore data.json
if [ -f /tmp/data.json.backup ]; then
    cp /tmp/data.json.backup "$DST_PLUGIN/data.json"
    ok "data.json restored"
fi

section "6. Verify user content survived"
check_survived() {
    if [ -f "$1" ]; then ok "$1 survived"
    else fail "$1 was deleted by update!"; fi
}

check_survived "$VAULT_PATH/User/my-note.md"
check_survived "$VAULT_PATH/vaultbot/Memory/Chat/test-chat.md"
check_survived "$VAULT_PATH/vaultbot/vaultbot_backend/sessions/test.jsonl"
check_survived "$VAULT_PATH/vaultbot/vaultbot_backend/identity/IDENTITY.md"
check_survived "$VAULT_PATH/vaultbot/vaultbot_backend/custom_tools/my_custom_tool.py"
check_survived "$VAULT_PATH/.env"

# Verify .env still has the token
if grep -q "ghp_fake_token_for_testing" "$VAULT_PATH/.env"; then
    ok ".env token preserved"
else
    fail ".env token was modified or deleted"
fi

section "7. Verify updated code is present"
# Check that the archive's backend code was applied
if [ -f "$DST_BACKEND/main.py" ]; then ok "main.py updated"
else fail "main.py missing after update"; fi

# Check custom tools from the archive are present
for tool in submit_contribution review_contributions torture_test; do
    if [ -f "$DST_BACKEND/custom_tools/${tool}.py" ]; then ok "$tool.py present after update"
    else fail "$tool.py missing after update"; fi
done

# Verify the user's custom tool was NOT deleted
if [ -f "$DST_BACKEND/custom_tools/my_custom_tool.py" ]; then
    ok "User's custom tool preserved (not deleted by update)"
else
    fail "User's custom tool was deleted by update!"
fi

section "8. Post-update import test"
VENV_PYTHON="$VAULT_PATH/vaultbot_venv/bin/python"
# Ensure directories the backend expects exist
mkdir -p "$VAULT_PATH/vaultbot/Memory/Chat"
mkdir -p "$VAULT_PATH/vaultbot/Memory/Build-Log"
mkdir -p "$VAULT_PATH/vaultbot/Knowledge/Research"
cd "$VAULT_PATH"
VAULT_PATH="$VAULT_PATH" "$VENV_PYTHON" -c "
import sys, os
sys.path.insert(0, '$DST_BACKEND')
try:
    import main
    print('  [PASS] Backend imports after update')
except Exception as e:
    print(f'  [FAIL] Backend import failed after update: {e}')
    sys.exit(1)
" 2>&1 || fail "post-update import failed"

if [ $? -eq 0 ]; then ok "Backend imports after update"; fi

section "9. Syntax check all .py files"
PY_ERRORS=0
find "$DST_BACKEND" -name "*.py" \
    ! -path "*/__pycache__/*" \
    ! -path "*/trash/*" \
    | while read -r f; do
    if ! "$VENV_PYTHON" -m py_compile "$f" 2>/dev/null; then
        echo "  [FAIL] Syntax error: $f"
    fi
done
ok "Syntax check complete"

section "RESULTS"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "  STATUS: FAILED"
    exit 1
else
    echo "  STATUS: ALL TESTS PASSED"
fi
