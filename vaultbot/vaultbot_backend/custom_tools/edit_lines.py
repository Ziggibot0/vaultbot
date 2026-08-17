"""
Agent-authored tool: edit_lines

Line-number-based editor for any file in the vault. Replaces a contiguous
range of lines (identified by 1-indexed line numbers) with new content.
This is the LLM-ERGONOMIC alternative to safe_write (full-file regeneration)
and safe_replace / md_safe_replace (exact-whitespace string matching).

WHY THIS EXISTS
----------------
LLMs are bad at two things that the existing edit tools require:
  1. Regenerating an entire file verbatim with small changes (safe_write,
     vault_safe_write, js_safe_write). The model drops lines, mangles
     indentation, hallucines missing boilerplate, and truncates long files.
  2. Reproducing exact strings including whitespace for old_str matching
     (safe_replace, md_safe_replace, js_safe_replace). The model normalizes
     tabs/spaces, strips trailing whitespace, and doesn't know if a string
     is unique in the file.

edit_lines sidesteps both problems: the model reads the file (via code_read
or vault_read_note, which return line numbers), identifies the line range
to change, and provides only the replacement content. No full-file
regeneration, no exact-string matching — just "replace lines 45-52 with
this".

DELEGATION
----------
For .py files: delegates to safe_writer.safe_write() via SelfImprover —
gets full syntax check, import verification, and auto-rollback.
For .js/.mjs/.cjs files: delegates to SelfImprover.js_safe_write() —
gets node --check syntax validation.
For .md files: implements the same backup + locked-note + sacred-journal
  protection as md_safe_replace, then writes atomically.
For other files: backs up to trash/, writes atomically.

USAGE
-----
- To EDIT lines: replace lines 45-52 with new_content.
- To INSERT after line N: replace lines N+1 to N (empty range) — but
  this tool requires start_line <= end_line. Instead, replace line N
  with "original line N content\nnew inserted content".
- To DELETE lines: replace lines N-M with empty string ("").
"""

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

# custom_tools/edit_lines.py -> parent = custom_tools
# -> parent.parent = vaultbot_backend
# -> parent.parent.parent = vaultbot/ (framework root)
# -> parent.parent.parent.parent = vault root
try:
    BACKEND_DIR = Path(__file__).resolve().parent.parent
except NameError:
    BACKEND_DIR = Path.cwd()
VAULT_ROOT = BACKEND_DIR.parent.parent  # the vault root
TRASH_DIR = BACKEND_DIR / "trash"  # vaultbot/vaultbot_backend/trash/

SCHEMA = {
    "name": "edit_lines",
    "description": (
        "Edit a file by replacing a range of lines identified by LINE NUMBERS. "
        "This is the EASIEST and most reliable edit tool: read the file with "
        "code_read or vault_read_note (which show line numbers), then call "
        "edit_lines with the start_line and end_line of the section you want "
        "to replace. No need to reproduce exact strings or regenerate the "
        "full file. PREFER this tool over safe_write, vault_safe_write, "
        "safe_replace, and md_safe_replace for any edit to an EXISTING file. "
        "For .py files: full syntax check + import verification + auto-rollback "
        "on failure. For .md files: backup to trash/ + locked-note protection. "
        "For .js files: node --check syntax validation. "
        "To DELETE lines: pass new_content='' (empty string). "
        "To INSERT: replace line N with its own content plus the new content "
        "on the lines after it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path to the file, relative to vault root "
                    "(e.g. 'vaultbot/vaultbot_backend/chat_handler.py' "
                    "or 'vaultbot/System/Procedures/My-Procedure.md')."
                ),
            },
            "start_line": {
                "type": "integer",
                "description": (
                    "1-indexed line number of the first line to replace. "
                    "Must be >= 1 and <= total lines in the file."
                ),
            },
            "end_line": {
                "type": "integer",
                "description": (
                    "1-indexed line number of the last line to replace "
                    "(inclusive). Must be >= start_line."
                ),
            },
            "new_content": {
                "type": "string",
                "description": (
                    "The replacement content for the specified line range. "
                    "Can contain multiple lines (separated by \\n). "
                    "Pass empty string ('') to delete the lines entirely."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, preview the edit result and run validation "
                    "checks without writing to disk."
                ),
            },
        },
        "required": ["file_path", "start_line", "end_line", "new_content"],
    },
}


def _is_path_traversal(file_path: str, vault_root: Path) -> bool:
    """Check if a resolved path would escape the vault root."""
    resolved = (vault_root / file_path).resolve()
    try:
        resolved.relative_to(vault_root.resolve())
        return False
    except ValueError:
        return True


def _is_sacred_journal(file_path: Path) -> bool:
    """Check if the filename is a date-only filename (personal journal)."""
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


def _apply_line_replacement(
    existing_content: str, start_line: int, end_line: int, new_content: str
) -> str:
    """Replace lines [start_line, end_line] (1-indexed, inclusive) with
    new_content. Returns the full new file content."""
    lines = existing_content.splitlines(keepends=True)
    # Handle files that don't end with a newline — splitlines(keepends=True)
    # preserves the last line's newline if present, or not if absent.
    # We need to be careful: the new_content may or may not end with \n.
    total = len(lines)

    if start_line < 1 or start_line > total:
        raise ValueError(
            f"start_line {start_line} is out of range (file has {total} lines)"
        )
    if end_line < start_line:
        raise ValueError(
            f"end_line ({end_line}) must be >= start_line ({start_line})"
        )
    if end_line > total:
        # Allow replacing up to the end of file (clamped), but warn.
        end_line = total

    # Build the new content:
    #   lines[0 : start_line-1]  → keep (before the replaced range)
    #   new_content              → insert
    #   lines[end_line : ]       → keep (after the replaced range)
    before = lines[: start_line - 1]
    after = lines[end_line:]

    # Ensure new_content ends with a newline if it's non-empty and the
    # surrounding lines have newlines. We want the result to be clean.
    new_lines_str = new_content
    if new_lines_str and not new_lines_str.endswith("\n"):
        # Check if the original line at start_line had a newline
        orig_had_newline = lines[start_line - 1].endswith("\n") if start_line <= total else True
        if orig_had_newline:
            new_lines_str = new_lines_str + "\n"

    result = "".join(before) + new_lines_str + "".join(after)
    return result


def _write_md_safe(
    file_path_str: str,
    full_path: Path,
    new_content: str,
    dry_run: bool,
) -> dict:
    """Write a .md file with backup, locked-note check, and atomic write.
    Mirrors md_safe_replace.py's safety pattern."""
    existing = full_path.read_text(encoding="utf-8")

    # Block LOCKED notes
    if _is_locked(existing):
        return {
            "status": "blocked",
            "error": f"Note is LOCKED — cannot edit: {file_path_str}",
        }

    # Block sacred journals
    if _is_sacred_journal(full_path):
        return {
            "status": "blocked",
            "error": f"Sacred journal file — cannot edit: {file_path_str}",
        }

    # Schema validation (if available)
    try:
        from note_schema import validate_schema

        ok, errors, warnings = validate_schema(new_content)
        if not ok:
            return {
                "status": "blocked",
                "error": f"Schema validation failed: {'; '.join(errors)}",
                "warnings": warnings,
            }
    except ImportError:
        pass  # schema validation optional

    if dry_run:
        return {
            "status": "dry_run",
            "file_path": str(full_path),
            "bytes_written": len(new_content),
            "checks": {"would_backup": True},
        }

    # Ensure parent exists
    full_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_name = f"{full_path.stem}_{timestamp}.md"
    backup_path = TRASH_DIR / backup_name
    shutil.copy2(str(full_path), str(backup_path))

    # Atomic write: temp file + rename
    fd, temp_path = tempfile.mkstemp(
        dir=str(full_path.parent), suffix=".tmp", prefix=full_path.stem + "_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(temp_path, str(full_path))
    except Exception as e:  # noqa: BLE001
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return {"status": "error", "error": f"Write failed: {e}"}

    return {
        "status": "written",
        "file_path": str(full_path),
        "bytes_written": len(new_content),
        "backup_path": str(backup_path.relative_to(VAULT_ROOT)),
    }


def _write_generic(
    full_path: Path,
    new_content: str,
    dry_run: bool,
) -> dict:
    """Write a non-.py, non-.md, non-.js file with a backup to trash/."""
    if dry_run:
        return {
            "status": "dry_run",
            "file_path": str(full_path),
            "bytes_written": len(new_content),
            "checks": {"would_backup": True},
        }

    # Backup
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_name = f"{full_path.stem}_{timestamp}{full_path.suffix}"
    backup_path = TRASH_DIR / backup_name
    shutil.copy2(str(full_path), str(backup_path))

    # Atomic write
    fd, temp_path = tempfile.mkstemp(
        dir=str(full_path.parent), suffix=".tmp", prefix=full_path.stem + "_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(temp_path, str(full_path))
    except Exception as e:  # noqa: BLE001
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return {"status": "error", "error": f"Write failed: {e}"}

    return {
        "status": "written",
        "file_path": str(full_path),
        "bytes_written": len(new_content),
        "backup_path": str(backup_path.relative_to(VAULT_ROOT)),
    }


def run(args: dict) -> dict:
    """Replace a line range in a file with new content.

    Reads the file, replaces lines [start_line, end_line] with new_content,
    then delegates to the appropriate safe-write pipeline based on file
    extension.

    Returns a dict with:
        status: "written" | "dry_run" | "blocked" | "error"
        file_path: the path written to
        bytes_written: size of content written
        lines_replaced: number of original lines replaced
        backup_path: path to backup (if one was made)
    """
    import sys

    file_path_str = args.get("file_path", "")
    start_line = int(args.get("start_line", 0))
    end_line = int(args.get("end_line", 0))
    new_content = args.get("new_content", "")
    dry_run = args.get("dry_run", False)

    # --- Validation ---

    # 1. file_path must be provided
    if not file_path_str:
        return {"status": "error", "error": "No file_path provided"}

    # 2. start_line and end_line must be valid
    if start_line < 1:
        return {"status": "error", "error": f"start_line must be >= 1 (got {start_line})"}
    if end_line < start_line:
        return {
            "status": "error",
            "error": f"end_line ({end_line}) must be >= start_line ({start_line})",
        }

    # 3. Path traversal check
    if _is_path_traversal(file_path_str, VAULT_ROOT):
        return {
            "status": "error",
            "error": f"Path traversal detected: {file_path_str} resolves outside vault root",
        }

    full_path = (VAULT_ROOT / file_path_str).resolve()

    # 4. File must exist
    if not full_path.exists():
        return {"status": "error", "error": f"File not found: {file_path_str}"}

    # 4a. Safe Mode content-aware gate: block edits to source-code files.
    # edit_lines is dual-use (edits .md notes AND .py/.js source), so it's
    # NOT in _DANGEROUS_TOOLS. Instead, this gate blocks source-code
    # extensions in Safe Mode while allowing .md and other non-code edits.
    # See safe_mode.py → is_file_edit_allowed().
    try:
        from safe_mode import is_file_edit_allowed, blocked_file_edit_message

        if not is_file_edit_allowed(file_path_str):
            msg = blocked_file_edit_message(file_path_str)
            return {"status": "blocked", "safe_mode_blocked": True, "error": msg}
    except ImportError:
        pass  # safe_mode not available — don't block (shouldn't happen)

    # 5. Read existing content
    try:
        existing_content = full_path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Read failed: {e}"}

    total_lines = len(existing_content.splitlines())

    # 6. start_line must be within range
    if start_line > total_lines:
        return {
            "status": "error",
            "error": f"start_line ({start_line}) exceeds file length ({total_lines} lines)",
        }

    # 7. Clamp end_line to file length
    effective_end = min(end_line, total_lines)
    lines_replaced = effective_end - start_line + 1

    # 8. Apply the line replacement
    try:
        new_full_content = _apply_line_replacement(
            existing_content, start_line, effective_end, new_content
        )
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    # 9. No-op check
    if new_full_content == existing_content:
        return {
            "status": "no_change",
            "error": "The edit produces no change to the file (new_content is identical to the original lines)",
        }

    # --- Delegate to the appropriate safe-write pipeline ---

    suffix = full_path.suffix.lower()

    if suffix == ".py":
        # Delegate to SelfImprover.safe_write for full syntax + import
        # verification + auto-rollback.
        backend_dir = str(
            BACKEND_DIR
        )
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from self_improver import SelfImprover

        _si = SelfImprover(session_logger=None)
        result = _si.safe_write(
            file_path=file_path_str,
            content=new_full_content,
            dry_run=dry_run,
        )
        # Enrich with edit_lines metadata
        result["lines_replaced"] = lines_replaced
        result["start_line"] = start_line
        result["end_line"] = effective_end
        return result

    elif suffix in (".js", ".mjs", ".cjs"):
        # Delegate to SelfImprover.js_safe_write for node --check validation.
        backend_dir = str(BACKEND_DIR)
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from self_improver import SelfImprover

        _si = SelfImprover(session_logger=None)
        result = _si.js_safe_write(
            file_path=file_path_str,
            content=new_full_content,
            dry_run=dry_run,
        )
        result["lines_replaced"] = lines_replaced
        result["start_line"] = start_line
        result["end_line"] = effective_end
        return result

    elif suffix == ".md":
        result = _write_md_safe(file_path_str, full_path, new_full_content, dry_run)
        result["lines_replaced"] = lines_replaced
        result["start_line"] = start_line
        result["end_line"] = effective_end
        return result

    else:
        # Generic file: backup + atomic write
        result = _write_generic(full_path, new_full_content, dry_run)
        result["lines_replaced"] = lines_replaced
        result["start_line"] = start_line
        result["end_line"] = effective_end
        return result