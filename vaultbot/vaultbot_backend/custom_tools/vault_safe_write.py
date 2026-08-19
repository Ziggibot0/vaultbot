"""
Agent-authored tool: vault_safe_write
"""

SCHEMA = {
    "name": "vault_safe_write",
    "description": "SAFE self-edit of markdown notes (.md files) in the vault. Backs up existing content to vaultbot_backend/trash/ before overwriting. Validates content is non-empty markdown. Blocks writes to LOCKED notes and sacred journal files (date-only filenames). Blocks path traversal attempts. Writes atomically (temp file + rename). Use this INSTEAD of code_run with open() for any markdown note write \u2014 it's the safety layer for knowledge, just as safe_write is for code. IMPORTANT: VaultBot-generated content MUST go under vaultbot/ (e.g. 'vaultbot/Knowledge/Research/My-Note.md'). Only user-personal notes go in User/ (e.g. 'User/VaultBot Issues.md'). VaultBot's own directives and identity notes live under vaultbot/System/Identity/ (e.g. 'vaultbot/System/Identity/Autonomy-Directive.md'). NEVER create Knowledge/, Memory/, System/, or *-Directive.md at the vault root \u2014 those are gitignored hygiene zones.",
    "parameters": {
        "properties": {
            "content": {
                "description": "The full markdown content to write.",
                "type": "string",
            },
            "dry_run": {
                "description": "If true, validate and report what would happen but do not write to disk.",
                "type": "boolean",
            },
            "file_path": {
                "description": "Path to the note, relative to vault root. VaultBot notes go under vaultbot/ (e.g. 'vaultbot/Knowledge/Research/My-Note.md', 'vaultbot/Memory/Chat/Chat-Topic.md', 'vaultbot/System/Procedures/My-Procedure.md'). User-personal notes go in User/ (e.g. 'User/VaultBot Issues.md'). VaultBot's own directives go under vaultbot/System/Identity/ (e.g. 'vaultbot/System/Identity/Autonomy-Directive.md'). NEVER write to root-level Knowledge/, Memory/, System/, or *-Directive.md \u2014 always use the vaultbot/ prefix.",
                "type": "string",
            },
        },
        "required": ["file_path", "content"],
        "type": "object",
    },
}

"""
Agent-authored tool: vault_safe_write
Safe markdown write — backs up, validates, blocks protected files.
"""

import os
import re
import time
import shutil
import tempfile
from pathlib import Path

# Determine paths from this file's location
# custom_tools/vault_safe_write.py -> parent.parent = vaultbot/vaultbot_backend/
# -> parent.parent.parent = vaultbot/ -> parent.parent.parent.parent = Vault2/ (vault root)
try:
    BACKEND_DIR = (
        Path(__file__).resolve().parent.parent
    )  # vaultbot/vaultbot_backend/
except NameError:
    BACKEND_DIR = Path.cwd()
VAULT_ROOT = BACKEND_DIR.parent.parent  # the vault root
TRASH_DIR = BACKEND_DIR / "trash"  # vaultbot/vaultbot_backend/trash/


def _is_sacred_journal(file_path: Path) -> bool:
    """Check if the filename is a date-only filename (the operator's personal journal)."""
    stem = file_path.stem
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", stem))


def _is_locked(content: str) -> bool:
    """Check if existing content contains a LOCKED marker."""
    # Check for standalone LOCKED line or frontmatter LOCKED
    lines = content.split("\n")
    for line in lines:
        if line.strip() == "LOCKED":
            return True
        if line.strip().startswith("LOCKED:"):
            return True
    # Also check frontmatter
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


def _is_root_directive(file_path: str) -> bool:
    """Block VaultBot from writing its own directives to the vault root.

    Directives belong under vaultbot/System/Identity/, never at the root.
    A root-level directive is a *-Directive.md (or *-Communication-Preferences.md)
    with no directory component (e.g. 'Autonomy-Directive.md').
    """
    normalized = file_path.replace("\\", "/").lstrip("./")
    if "/" in normalized:
        return False  # nested under a subdirectory — allowed
    stem = Path(normalized).stem
    return (
        stem.endswith("-Directive")
        or stem.endswith("-Communication-Preferences")
        or stem == "Communication-Preferences"
    )


def run(args: dict) -> dict:
    """Safely write a markdown note with backup and validation.

    Returns a dict with:
        status: "written" | "blocked" | "dry_run"
        file_path: the path written to
        backup_path: path to backup (if one was made)
        bytes_written: size of content written
        checks: validation results
        blocked_reason: why the write was blocked (if applicable)
    """
    file_path_str = args.get("file_path", "")
    content = args.get("content", "")
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

    # 3. Path traversal check
    if _is_path_traversal(file_path_str, VAULT_ROOT):
        result["blocked_reason"] = (
            f"Path traversal detected: {file_path_str} resolves outside vault root"
        )
        return result

    # 3b. Block root-level directives — VaultBot's directives live under
    # vaultbot/System/Identity/, never at the vault root.
    if _is_root_directive(file_path_str):
        result["blocked_reason"] = (
            f"Root-level directive blocked: {file_path_str}. "
            "VaultBot directives belong under vaultbot/System/Identity/."
        )
        return result

    full_path = (VAULT_ROOT / file_path_str).resolve()
    result["checks"]["resolved_path"] = str(full_path)

    # 4. Content must not be empty
    if len(content.strip()) == 0:
        result["blocked_reason"] = "Content is empty"
        return result
    result["checks"]["content_length"] = len(content)

    # 5. Check if file exists → read for LOCKED check + backup
    file_exists = full_path.exists()
    result["checks"]["file_exists"] = file_exists

    if file_exists:
        with open(full_path, encoding="utf-8") as f:
            existing = f.read()

        # 6. Block LOCKED notes
        if _is_locked(existing):
            result["blocked_reason"] = (
                f"Note is LOCKED — cannot overwrite: {file_path_str}"
            )
            return result
        result["checks"]["is_locked"] = False

        # 7. Block sacred journals
        if _is_sacred_journal(full_path):
            result["blocked_reason"] = (
                f"Sacred journal file — cannot overwrite: {file_path_str}"
            )
            return result
        result["checks"]["is_sacred_journal"] = False

        # 8. Check if content is identical (no-op)
        if existing == content:
            result["status"] = "dry_run"
            result["blocked_reason"] = "Content identical to existing — no write needed"
            result["bytes_written"] = len(content)
            return result
        result["checks"]["content_changed"] = True
    else:
        # New file — check it's not a sacred journal name
        if _is_sacred_journal(full_path):
            result["blocked_reason"] = (
                f"Cannot create sacred journal file: {file_path_str}"
            )
            return result
        result["checks"]["is_sacred_journal"] = False

    # --- Schema injection (universal frontmatter) ---
    # Auto-inject missing required fields.  Validate to catch invalid values.
    try:
        from note_schema import inject_schema, validate_schema

        content = inject_schema(
            content,
            file_path_str,
            existing_content=existing if file_exists else None,
        )
        ok, errors, warnings = validate_schema(content)
        result["checks"]["schema_valid"] = ok
        result["checks"]["schema_warnings"] = warnings
        if not ok:
            result["blocked_reason"] = f"Schema validation failed: {'; '.join(errors)}"
            return result
    except ImportError:
        # note_schema not available (e.g. running outside backend dir) —
        # don't block the write, just note it.
        result["checks"]["schema_injection_skipped"] = True

    # --- All checks passed ---

    if dry_run:
        result["status"] = "dry_run"
        result["bytes_written"] = len(content)
        result["checks"]["would_backup"] = file_exists
        return result

    # Ensure parent directory exists
    full_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file if it exists
    if file_exists:
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
            f.write(content)
        # Atomic rename (on same filesystem)
        os.replace(temp_path, str(full_path))
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        # Clean up temp file on failure
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        result["blocked_reason"] = f"Write failed: {e}"
        return result

    result["status"] = "written"
    result["bytes_written"] = len(content)
    return result
