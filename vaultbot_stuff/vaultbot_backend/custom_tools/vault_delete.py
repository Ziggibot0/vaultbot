"""
Agent-authored tool: vault_delete
"""

SCHEMA = {"name": "vault_delete", "description": "Safely delete a note from the vault. Backs up content to vaultbot_backend/trash/ before deleting. Hard-blocks sacred journals (except empty past-day journals), LOCKED notes, and core identity files. Reports incoming wikilinks that will become broken after deletion. Use this to clean up junk files without risk.", "parameters": {"properties": {"file_path": {"description": "Path to the note to delete, relative to vault root (e.g. 'Other post.md')", "type": "string"}}, "required": ["file_path"], "type": "object"}}

import os
import re
from datetime import datetime, date
from pathlib import Path

VAULT_ROOT = Path(__file__).parent.parent.parent.parent.resolve()  # 4 levels up for vault root (vaultbot_stuff/vaultbot_backend/custom_tools/ -> the vault root)
EXCLUDE_DIRS = {".git", "node_modules", ".obsidian", "vaultbot_venv", "__pycache__", "checkpoints", ".venv"}
BACKEND_DIR = Path(__file__).parent.parent.resolve()  # vaultbot_stuff/vaultbot_backend/
TRASH_DIR = BACKEND_DIR / "trash"
IDENTITY_FILES = {"IDENTITY", "SELF_MODEL", "GOALS"}

def _is_sacred(stem: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", stem) or re.match(r"^\d{2}-\d{2}-\d{4}$", stem))

def _is_empty_past_journal(stem: str, full_path: str) -> bool:
    """Check if this is an empty journal from a past day (deletable).

    the operator authorized deleting empty past-day journals — they contain no
    thoughts, so there's nothing sacred to protect. Today's journal is
    always kept (the operator might still write in it). Non-empty journals are
    always kept (those are the operator's actual thoughts).
    """
    if not _is_sacred(stem):
        return False
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", stem)
    if not m:
        m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", stem)
        if not m:
            return False
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))

    try:
        journal_date = date(year, month, day)
    except ValueError:
        return False

    today = date.today()
    if journal_date >= today:
        return False  # Today or future — never delete

    try:
        with open(full_path, encoding='utf-8') as f:
            content = f.read().strip()
        return len(content) == 0
    except Exception:
        return False

def _is_locked(content: str) -> bool:
    lines = content.split("\n")
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            if re.match(r'^[\w-]+:\s*LOCKED\s*$', stripped, re.IGNORECASE):
                return True
            if re.match(r'^locked:\s*true\s*$', stripped, re.IGNORECASE):
                return True
        else:
            if stripped == "LOCKED":
                return True
    return False

def _find_incoming_links(target_stem: str) -> list:
    """Find all notes that wikilink to the target."""
    incoming = []
    for root, dirs, files in os.walk(VAULT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if not f.endswith(".md"):
                continue
            full = Path(root) / f
            rel = str(full.relative_to(VAULT_ROOT)).replace("\\", "/")
            if full.stem == target_stem:
                continue
            try:
                content = full.read_text(encoding="utf-8")
            except:
                continue
            wikilinks = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)
            for link in wikilinks:
                if link.strip() == target_stem:
                    incoming.append(rel)
                    break
    return incoming

def run(args: dict) -> dict:
    file_path = args.get("file_path", "")
    if not file_path:
        return {"error": "file_path is required"}

    full = (VAULT_ROOT / file_path).resolve()

    try:
        full.relative_to(VAULT_ROOT.resolve())
    except ValueError:
        return {"error": "path must be inside vault root"}

    if not full.exists():
        return {"error": f"file not found: {file_path}"}

    if not file_path.endswith(".md"):
        return {"error": "can only delete .md files"}

    stem = full.stem

    # Sacred journal check — but allow empty past-day journals
    if _is_sacred(stem):
        if _is_empty_past_journal(stem, str(full)):
            # Empty past journal — delete without backup (nothing to back up)
            full.unlink()
            return {
                "deleted": file_path,
                "backup": None,
                "bytes_deleted": 0,
                "incoming_links": [],
                "incoming_link_count": 0,
                "warning": None,
                "note": "empty past-day journal deleted (no content to back up)"
            }
        else:
            return {"error": f"BLOCKED: '{stem}' is a sacred journal file — never deletable"}

    if stem in IDENTITY_FILES:
        return {"error": f"BLOCKED: '{stem}' is a core identity file — never deletable"}

    content = full.read_text(encoding="utf-8")

    if _is_locked(content):
        return {"error": f"BLOCKED: '{stem}' is LOCKED — never deletable"}

    incoming_links = _find_incoming_links(stem)

    # Check if file is already in trash — skip re-backup
    is_in_trash = "vaultbot_stuff/vaultbot_backend" in file_path and "trash" in file_path

    if is_in_trash:
        # Already a backup — delete permanently without re-backing-up
        full.unlink()
        return {
            "deleted": file_path,
            "backup": None,
            "bytes_deleted": len(content),
            "incoming_links": incoming_links,
            "incoming_link_count": len(incoming_links),
            "warning": f"{len(incoming_links)} note(s) now have broken wikilinks to [[{stem}]]" if incoming_links else None,
            "note": "file was already in trash — deleted permanently without re-backup"
        }

    # Backup to trash before deleting
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"{stem}_{timestamp}.md"
    backup_path = TRASH_DIR / backup_name
    backup_path.write_text(content, encoding="utf-8")

    # Delete
    full.unlink()

    return {
        "deleted": file_path,
        "backup": str(backup_path.relative_to(VAULT_ROOT)).replace("\\", "/"),
        "bytes_deleted": len(content),
        "incoming_links": incoming_links,
        "incoming_link_count": len(incoming_links),
        "warning": f"{len(incoming_links)} note(s) now have broken wikilinks to [[{stem}]]" if incoming_links else None
    }