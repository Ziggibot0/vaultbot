"""
Agent-authored tool: safe_replace
"""

SCHEMA = {"name": "safe_replace", "description": "Safely replace a string in a backend Python file. Reads the file, replaces old_str with new_str, and writes via SelfImprover.safe_write(). Use for targeted edits to large files.", "parameters": {"properties": {"file_path": {"description": "Path to the file, relative to vault root", "type": "string"}, "new_str": {"description": "The replacement string", "type": "string"}, "old_str": {"description": "The exact string to find and replace", "type": "string"}}, "required": ["file_path", "old_str", "new_str"]}}

def run(args: dict) -> dict:
    from self_improver import SelfImprover
    from pathlib import Path

    BACKEND_DIR = Path(__file__).parent.parent.resolve()
    BACKEND_ROOT = BACKEND_DIR.parent.parent

    file_path = args["file_path"]
    old_str = args["old_str"]
    new_str = args["new_str"]

    # Resolve full path
    full = BACKEND_ROOT / file_path
    if not full.exists():
        return {"error": f"file not found: {full}"}

    content = full.read_text(encoding="utf-8")

    if old_str not in content:
        return {"error": "old_str not found in file", "old_str_preview": old_str[:200]}

    count = content.count(old_str)
    if count > 1:
        return {"error": f"old_str found {count} times — must be unique", "old_str_preview": old_str[:200]}

    new_content = content.replace(old_str, new_str)

    _si = SelfImprover(session_logger=None)
    result = _si.safe_write(file_path=file_path, content=new_content, dry_run=False)
    return result
