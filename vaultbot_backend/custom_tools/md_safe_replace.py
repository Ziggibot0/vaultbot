"""
Agent-authored tool: md_safe_replace

Targeted string replacement in markdown notes (.md files) in the vault.
The markdown counterpart to safe_replace (which only handles .py files).
Reads the note, replaces old_str with new_str, and writes atomically with
a backup. Use this for small surgical edits to procedure notes, knowledge
notes, or any .md file — instead of vault_safe_write (which requires the
FULL file content).

Safety (mirrors vault_safe_write):
  - Backs up existing content to vaultbot_backend/trash/ before overwriting.
  - Blocks writes to LOCKED notes and sacred journal files (date-only filenames).
  - Blocks path traversal.
  - Rejects if old_str is not found, or appears more than once (ambiguous).
  - Atomic write (temp file + rename).
"""

SCHEMA = {
    "name": "md_safe_replace",
    "description": (
        "Safely replace a string in a markdown note (.md file) in the vault. "
        "The markdown counterpart to safe_replace (which only handles .py). "
        "Reads the note, replaces old_str with new_str, writes atomically with "
        "a backup to trash/. Use this for targeted edits to procedure notes, "
        "knowledge notes, or any .md file — INSTEAD of vault_safe_write (which "
        "requires the FULL file content) when you only need to change one "
        "section. Blocks LOCKED notes and sacred journals. Rejects if old_str "
        "is not found or appears more than once (must be unique). "
        "IMPORTANT: md_safe_replace is for MARKDOWN (.md) files only. "
        "For Python (.py) files, use safe_replace or safe_write instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path to the .md note, relative to vault root. "
                    "E.g. 'vaultbot-stuff/System/Procedures/Check-Error-Handling.md'."
                ),
            },
            "old_str": {
                "type": "string",
                "description": "The exact string to find. Must be unique in the file.",
            },
            "new_str": {
                "type": "string",
                "description": "The replacement string.",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, verify the replace would work but do not write."
                ),
            },
        },
        "required": ["file_path", "old_str", "new_str"],
    },
}


import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from paths import VAULT_ROOT  # noqa: E402

# custom_tools/md_safe_replace.py -> parent.parent = vaultbot_backend/
try:
    BACKEND_DIR = Path(__file__).resolve().parent.parent  # vaultbot_backend/
except NameError:
    BACKEND_DIR = Path.cwd()
TRASH_DIR = BACKEND_DIR / "trash"  # vaultbot_backend/trash/


def _is_sacred_journal(file_path: Path) -> bool:
    """Check if the filename is a date-only filename (the operator's personal
    journal)."""
    stem = file_path.stem
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", stem))


def _is_locked(content: str) -> bool:
    """Check if existing content contains a LOCKED marker."""
    lines = content.split("\n")
    for line in lines:
        if line.strip() == "LOCKED":
            return True
        if line.strip().startswith("LOCKED:"):
            return True
    if content.strip().startswith("---"):
        fm_end = content.find("---", 3)
        if fm_end != -1:
            fm = content[3:fm_end]
            if "LOCKED" in fm:
                return True
    return False


def _is_path_traversal(file_path: str, vault_root: Path) -> bool:
    """Check if a resolved path would escape the vault root."""
    resolved = (vault_root / file_path).resolve()
    try:
        resolved.relative_to(vault_root.resolve())
        return False
    except ValueError:
        return True


def run(args: dict) -> dict:
    """Safely replace a string in a markdown note.

    Returns a dict with:
        status: "written" | "blocked" | "dry_run" | "no_change"
        file_path: the path written to
        backup_path: path to backup (if one was made)
        bytes_written: size of content written
        checks: validation results
        blocked_reason: why the write was blocked (if applicable)
    """
    file_path_str = args.get("file_path", "")
    old_str = args.get("old_str", "")
    new_str = args.get("new_str", "")
    dry_run = args.get("dry_run", False)

    result = {
        "status": "blocked",
        "file_path": file_path_str,
        "backup_path": None,
        "bytes_written": 0,
        "checks": {},
        "blocked_reason": None,
    }

    # --- Validation ---

    # 1. Path must be provided
    if not file_path_str:
        result["blocked_reason"] = "No file_path provided"
        return result

    # 2. Must be a .md file
    if not file_path_str.endswith(".md"):
        result["blocked_reason"] = f"File must be a .md file (got: {file_path_str})"
        return result

    # 3. old_str must be provided and non-empty
    if not old_str:
        result["blocked_reason"] = "old_str is empty — nothing to replace"
        return result

    # 4. Path traversal check
    if _is_path_traversal(file_path_str, VAULT_ROOT):
        result["blocked_reason"] = (
            f"Path traversal detected: {file_path_str} resolves outside vault root"
        )
        return result

    full_path = (VAULT_ROOT / file_path_str).resolve()
    result["checks"]["resolved_path"] = str(full_path)

    # 5. File must exist
    if not full_path.exists():
        result["blocked_reason"] = f"File not found: {file_path_str}"
        return result
    result["checks"]["file_exists"] = True

    # 6. Read existing content
    existing = full_path.read_text(encoding="utf-8")

    # 7. Block LOCKED notes
    if _is_locked(existing):
        result["blocked_reason"] = f"Note is LOCKED — cannot edit: {file_path_str}"
        return result
    result["checks"]["is_locked"] = False

    # 8. Block sacred journals
    if _is_sacred_journal(full_path):
        result["blocked_reason"] = f"Sacred journal file — cannot edit: {file_path_str}"
        return result
    result["checks"]["is_sacred_journal"] = False

    # 9. old_str must be present
    if old_str not in existing:
        result["blocked_reason"] = "old_str not found in file"
        result["checks"]["old_str_preview"] = old_str[:200]
        return result
    result["checks"]["old_str_found"] = True

    # 10. old_str must be unique (exactly one occurrence)
    count = existing.count(old_str)
    if count > 1:
        result["blocked_reason"] = (
            f"old_str found {count} times — must be unique (appears in multiple places)"
        )
        result["checks"]["old_str_count"] = count
        result["checks"]["old_str_preview"] = old_str[:200]
        return result
    result["checks"]["old_str_count"] = 1

    # 11. No-op if old_str == new_str
    if old_str == new_str:
        result["status"] = "no_change"
        result["blocked_reason"] = (
            "old_str and new_str are identical — no replacement needed"
        )
        return result

    # --- Perform the replacement ---
    new_content = existing.replace(old_str, new_str, 1)
    result["checks"]["content_changed"] = True

    # --- Schema validation (same as vault_safe_write) ---
    try:
        from note_schema import validate_schema

        ok, errors, warnings = validate_schema(new_content)
        result["checks"]["schema_valid"] = ok
        result["checks"]["schema_warnings"] = warnings
        if not ok:
            result["blocked_reason"] = f"Schema validation failed: {'; '.join(errors)}"
            return result
    except ImportError:
        result["checks"]["schema_validation_skipped"] = True

    # --- All checks passed ---

    if dry_run:
        result["status"] = "dry_run"
        result["bytes_written"] = len(new_content)
        result["checks"]["would_backup"] = True
        return result

    # Ensure parent directory exists (should already, since file exists)
    full_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_name = f"{full_path.stem}_{timestamp}.md"
    backup_path = TRASH_DIR / backup_name
    shutil.copy2(str(full_path), str(backup_path))
    result["backup_path"] = str(backup_path.relative_to(VAULT_ROOT))
    result["checks"]["backup_created"] = True

    # Atomic write: write to temp file, then rename
    fd, temp_path = tempfile.mkstemp(
        dir=str(full_path.parent), suffix=".tmp", prefix=full_path.stem + "_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(temp_path, str(full_path))
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        result["blocked_reason"] = f"Write failed: {e}"
        return result

    result["status"] = "written"
    result["bytes_written"] = len(new_content)
    return result
