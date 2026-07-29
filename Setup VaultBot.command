#!/bin/bash
# ===================================================================
#  VaultBot one-click setup wizard (macOS / Linux)
#  Double-click this file (in Finder, right-click → Open) to set up
#  VaultBot. No terminal skills needed.
# ===================================================================
set -e
cd "$(dirname "$0")"

echo
echo "============================================================"
echo "  VaultBot Setup Wizard"
echo "============================================================"
echo

# Prefer python3 (the default on macOS Homebrew / system), then python.
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo
    echo "[ERROR] Python was not found on this computer."
    echo
    echo "VaultBot needs Python 3.11 or newer. Install it from:"
    echo "  https://www.python.org/downloads/"
    echo "  (or: brew install python@3.12)"
    echo
    read -p "Press Enter to close…"
    exit 1
fi

"$PY" "$(dirname "$0")/setup_wizard.py"

echo
read -p "Press Enter to close…"