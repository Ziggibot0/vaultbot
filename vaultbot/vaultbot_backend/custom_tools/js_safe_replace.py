"""
Agent-authored tool: js_safe_replace
"""

SCHEMA = {
    "name": "js_safe_replace",
    "description": "Safely replace a string in a JavaScript file. Reads the file, replaces old_str with new_str, validates with node --check, and writes atomically.",
    "parameters": {
        "properties": {
            "file_path": {
                "description": "Path to the .js file, relative to vault root",
                "type": "string",
            },
            "new_str": {"description": "The replacement string", "type": "string"},
            "old_str": {"description": "The exact string to find", "type": "string"},
        },
        "required": ["file_path", "old_str", "new_str"],
        "type": "object",
    },
}


def run(args: dict) -> dict:
    import os
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    file_path = args.get("file_path", "")
    old_str = args.get("old_str", "")
    new_str = args.get("new_str", "")

    if not file_path or not old_str:
        return {"error": "file_path and old_str are required"}

    vault_root = Path(__file__).resolve().parent.parent.parent.parent
    full_path = vault_root / file_path

    if not full_path.exists():
        return {"error": f"File not found: {full_path}"}

    content = full_path.read_text(encoding="utf-8")

    count = content.count(old_str)
    if count == 0:
        return {"error": "old_str not found in file"}
    if count > 1:
        return {"error": f"old_str appears {count} times — must be unique"}

    new_content = content.replace(old_str, new_str)

    # Validate JS syntax with node --check
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    )
    tmp.write(new_content)
    tmp.close()
    try:
        result = subprocess.run(
            ["node", "--check", tmp.name], capture_output=True, timeout=10
        )
        if result.returncode != 0:
            return {
                "error": f"node --check failed: {result.stderr.decode('utf-8', errors='replace')}"
            }
    except FileNotFoundError:
        # node not available — skip validation
        pass
    except Exception as e:
        return {"error": f"node --check error: {e}"}
    finally:
        os.unlink(tmp.name)

    # Backup
    trash_dir = vault_root / "vaultbot" / "vaultbot_backend" / "trash"
    trash_dir.mkdir(exist_ok=True)
    backup_name = full_path.stem + "_js_replace_backup" + full_path.suffix
    shutil.copy2(full_path, trash_dir / backup_name)

    # Write atomically
    tmp_write = str(full_path) + ".tmp"
    Path(tmp_write).write_text(new_content, encoding="utf-8")
    os.replace(tmp_write, str(full_path))

    return {
        "status": "ok",
        "file_path": str(full_path),
        "bytes": len(new_content.encode("utf-8")),
    }
