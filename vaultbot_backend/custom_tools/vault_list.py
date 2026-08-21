"""
Agent-authored tool: vault_list
"""

SCHEMA = {
    "name": "vault_list",
    "description": (
        "List all .md files in the vault. Optionally filter by directory or "
        "tag. Returns filenames relative to vault root. Use this to see what "
        "notes exist — complements semantic search when you need to know "
        "what's actually in the vault."
    ),
    "parameters": {
        "properties": {
            "directory": {
                "description": (
                    "Optional subdirectory to search within (e.g. 'vaultbot/chat')"
                ),
                "type": "string",
            },
            "tag": {
                "description": (
                    "Optional tag to filter by (checks for #tag in note content)"
                ),
                "type": "string",
            },
        },
        "required": [],
        "type": "object",
    },
}

import os  # noqa: E402
from pathlib import Path  # noqa: E402

from paths import VAULT_ROOT  # noqa: E402

# The vault root is resolved centrally in paths.py (vault is a sibling of
# vaultbot_backend/, not an ancestor).
EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    ".obsidian",
    "vaultbot_venv",
    "__pycache__",
    "checkpoints",
    ".venv",
}


def run(args: dict) -> dict:
    directory = args.get("directory", "")
    tag = args.get("tag", "")

    search_path = VAULT_ROOT / directory if directory else VAULT_ROOT
    if not search_path.exists():
        return {"error": f"directory not found: {directory}"}

    md_files = []
    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith(".md"):
                full = Path(root) / f
                rel = full.relative_to(VAULT_ROOT)
                md_files.append(str(rel).replace("\\", "/"))

    md_files.sort()

    if tag:
        filtered = []
        for f in md_files:
            try:
                content = (VAULT_ROOT / f).read_text(encoding="utf-8")
                if f"#{tag}" in content:
                    filtered.append(f)
            except Exception:  # noqa: BLE001 — best-effort: unreadable file is skipped
                pass
        md_files = filtered

    return {"count": len(md_files), "files": md_files}
