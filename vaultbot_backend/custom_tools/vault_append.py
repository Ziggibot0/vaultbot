"""
Agent-authored tool: vault_append
"""

SCHEMA = {"name": "vault_append", "description": "Append content to an existing note without overwriting it. Safer than code_write for incremental updates \u2014 preserves all existing content and adds new content at the end. Respects LOCKED notes (standalone line or frontmatter marker) and sacred journal files (date-only filenames).", "parameters": {"properties": {"content": {"description": "Content to append to the note", "type": "string"}, "file_path": {"description": "Path to the note, relative to vault root (e.g. 'Autonomy-Directive.md')", "type": "string"}}, "required": ["file_path", "content"], "type": "object"}}

import re
from pathlib import Path

VAULT_ROOT = Path(__file__).parent.parent.parent.resolve()

def _is_locked(content: str) -> bool:
    """Check if a note is LOCKED — standalone line or frontmatter field."""
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

def run(args: dict) -> dict:
    file_path = args.get("file_path", "")
    content = args.get("content", "")
    
    if not file_path or not content:
        return {"error": "file_path and content are required"}
    
    full = (VAULT_ROOT / file_path).resolve()
    
    try:
        full.relative_to(VAULT_ROOT.resolve())
    except ValueError:
        return {"error": "path must be inside vault root"}
    
    if full.exists():
        existing = full.read_text(encoding="utf-8")
        if _is_locked(existing):
            return {"error": "note is LOCKED — cannot append"}
        stem = full.stem
        if re.match(r"^\d{4}-\d{2}-\d{2}$", stem) or re.match(r"^\d{2}-\d{2}-\d{4}$", stem):
            return {"error": "date-only filenames are sacred journal entries — cannot append"}
        new_content = existing.rstrip() + "\n\n" + content + "\n"
    else:
        new_content = content + "\n"
    
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(new_content, encoding="utf-8")
    
    return {"file_path": str(full), "bytes_added": len(content), "total_bytes": len(new_content)}
