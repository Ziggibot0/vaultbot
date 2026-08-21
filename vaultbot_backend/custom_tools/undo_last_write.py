"""
Agent-authored tool: undo_last_write

Undo the most recent vault_safe_write or vault_delete operation by restoring
from the trash backup. The trash directory (vaultbot_backend/trash/) stores
a backup of every file before it's overwritten or deleted. This tool finds
the most recent backup and restores it.
"""

SCHEMA = {
    "name": "undo_last_write",
    "description": (
        "Undo the most recent vault write or delete operation. Restores the "
        "last backed-up file from the trash directory. Use this when you or "
        "the user wants to reverse a recent change to a vault note. Only "
        "works for operations that created a trash backup (vault_safe_write, "
        "vault_delete). Does NOT undo code edits (safe_write has its own "
        "rollback via git_rollback)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean",
                "description": (
                    "If true, show what would be restored without actually "
                    "restoring it."
                ),
            },
        },
    },
}


def run(args: dict) -> dict:
    """Undo the most recent vault write/delete by restoring from trash.

    Scans the trash directory for the most recently modified backup file,
    restores it to its original location, and removes the backup.

    Returns a dict with:
        status: "restored" | "nothing_to_undo" | "dry_run"
        restored_path: the path that was restored
        backup_path: the trash backup that was used
    """
    import os
    import shutil
    from pathlib import Path

    from paths import VAULT_ROOT

    dry_run = args.get("dry_run", False)

    # Determine paths.
    try:
        backend_dir = Path(__file__).resolve().parent.parent
    except NameError:
        backend_dir = Path.cwd()
    vault_root = VAULT_ROOT
    trash_dir = backend_dir / "trash"

    if not trash_dir.exists() or not trash_dir.is_dir():
        return {
            "status": "nothing_to_undo",
            "message": "Trash directory is empty or doesn't exist.",
        }

    # Find the most recently modified backup file in trash (recursively).
    newest = None
    newest_mtime = 0
    for f in trash_dir.rglob("*"):
        if not f.is_file():
            continue
        try:
            mtime = f.stat().st_mtime
            if mtime > newest_mtime:
                newest_mtime = mtime
                newest = f
        except OSError:
            continue

    if newest is None:
        return {
            "status": "nothing_to_undo",
            "message": "No backup files found in trash.",
        }

    # Determine the original path from the backup filename.
    # Backups are stored with the relative path flattened (slashes → underscores).
    # Example: Knowledge/Research/My-Note.md
    #       → vaultbot_Knowledge_Research_My-Note.md.bak
    backup_name = newest.name
    # Strip the .bak suffix.
    original_rel = backup_name[:-4] if backup_name.endswith(".bak") else backup_name

    # Try to reconstruct the original path.
    # The backup name uses underscores in place of path separators.
    original_path = None

    # Try to find the original by checking if the reconstructed path exists
    # or if the backup contains metadata about the original location.
    # First, check if the backup file itself has a comment with the original path.
    try:
        content = newest.read_text(encoding="utf-8", errors="replace")
        # vault_safe_write backups include a header comment with the original path.
        for line in content.split("\n")[:5]:
            if line.startswith("<!-- original_path: "):
                candidate = (
                    line[len("<!-- original_path: ") :].removesuffix(" -->").strip()
                )
                candidate_path = vault_root / candidate
                # The original might not exist anymore (it was deleted/overwritten),
                # so we just use the path from the backup header.
                original_path = candidate_path
                break
    except Exception:  # noqa: BLE001 — best-effort header parsing; fall through to filename reconstruction
        pass

    # If no header found, try to reconstruct from the backup name.
    if original_path is None:
        # Try to find a matching file by walking the vault.
        for root, dirs, files in os.walk(vault_root):
            # Skip excluded dirs.
            dirs[:] = [
                d
                for d in dirs
                if d
                not in {".git", ".venv", ".obsidian", "__pycache__", "node_modules"}
            ]
            for f in files:
                if not f.endswith(".md"):
                    continue
                full = Path(root) / f
                try:
                    rel = (
                        str(full.relative_to(vault_root))
                        .replace("\\", "/")
                        .replace("/", "_")
                    )
                except ValueError:
                    continue
                if rel == original_rel or f == original_rel:
                    original_path = full
                    break
            if original_path:
                break

    if original_path is None:
        return {
            "status": "error",
            "message": f"Could not determine original path for backup: {backup_name}",
            "backup_path": str(newest),
        }

    if dry_run:
        return {
            "status": "dry_run",
            "would_restore": str(original_path),
            "backup_path": str(newest),
            "backup_size": newest.stat().st_size,
            "message": f"Would restore {original_path.name} from trash backup.",
        }

    # Restore: copy backup to original location, then remove backup.
    try:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(newest, original_path)
        newest.unlink()
        return {
            "status": "restored",
            "restored_path": str(original_path),
            "backup_path": str(newest),
            "message": f"Restored {original_path.name} from trash backup.",
        }
    except Exception as e:  # noqa: BLE001 — best-effort: restore failure returns error to caller
        return {
            "status": "error",
            "message": f"Failed to restore: {e}",
            "restored_path": str(original_path),
            "backup_path": str(newest),
        }
