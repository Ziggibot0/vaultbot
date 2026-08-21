"""
Agent-authored tool: vault_append
"""

SCHEMA = {
    "name": "vault_append",
    "description": (
        "Append content to an existing note without overwriting it. Safer "
        "than code_write for incremental updates — preserves all existing "
        "content and adds new content at the end. Respects LOCKED notes "
        "(standalone line or frontmatter marker) and sacred journal files "
        "(date-only filenames). IMPORTANT: VaultBot-generated content lives "
        "under the vault root (e.g. 'Knowledge/Research/My-Note.md'). "
        "Only user-personal notes go in User/ (e.g. 'User/Research-Roadmap.md'). "
        "VaultBot's own directives and identity notes live under "
        "System/Identity/ (e.g. "
        "'System/Identity/Autonomy-Directive.md'). NEVER create "
        "Knowledge/, Memory/, System/, or directive notes at the vault root."
    ),
    "parameters": {
        "properties": {
            "content": {
                "description": "Content to append to the note",
                "type": "string",
            },
            "file_path": {
                "description": (
                    "Path to the note, relative to vault root. VaultBot notes "
                    "are under the vault root (e.g. "
                    "'Memory/Chat/Chat-Topic.md'). User-personal notes "
                    "go in User/ (e.g. 'User/Research-Roadmap.md'). VaultBot "
                    "notes (including its own directives) are under "
                    "System/Identity/ (e.g. 'System/Identity/Autonomy-Directive.md')."
                ),
                "type": "string",
            },
        },
        "required": ["file_path", "content"],
        "type": "object",
    },
}

import re  # noqa: E402
from pathlib import Path  # noqa: E402

# Central path resolution: VAULT_ROOT (user vault) + FRAMEWORK_ROOT (git repo).
# resolve_content_path maps a logical path to the correct physical root.
from paths import resolve_content_path, is_within_content_roots  # noqa: E402


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
            if re.match(r"^[\w-]+:\s*LOCKED\s*$", stripped, re.IGNORECASE):
                return True
            if re.match(r"^locked:\s*true\s*$", stripped, re.IGNORECASE):
                return True
        else:
            if stripped == "LOCKED":
                return True
    return False


def _is_root_directive(file_path: str) -> bool:
    """Block VaultBot from writing its own directives to the vault root.

    Directives belong under System/Identity/ (framework root), never at the
    vault root. A root-level directive is a *-Directive.md (or
    *-Communication-Preferences.md) with no directory component (e.g.
    'Autonomy-Directive.md').
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
    file_path = args.get("file_path", "")
    content = args.get("content", "")

    if not file_path or not content:
        return {"error": "file_path and content are required"}

    # Block root-level directives — VaultBot's directives live under
    # System/Identity/, never at the vault root.
    if _is_root_directive(file_path):
        return {
            "error": (
                f"Root-level directive blocked: {file_path}. "
                "VaultBot directives belong under System/Identity/."
            )
        }

    full = resolve_content_path(file_path)

    if not is_within_content_roots(full):
        return {"error": "path must be inside vault root"}

    if full.exists():
        existing = full.read_text(encoding="utf-8")
        if _is_locked(existing):
            return {"error": "note is LOCKED — cannot append"}
        stem = full.stem
        if re.match(r"^\d{4}-\d{2}-\d{2}$", stem) or re.match(
            r"^\d{2}-\d{2}-\d{4}$", stem
        ):
            return {
                "error": (
                    "date-only filenames are sacred journal entries — cannot append"
                )
            }
        new_content = existing.rstrip() + "\n\n" + content + "\n"
    else:
        new_content = content + "\n"

    # Inject schema on the merged content so the whole note stays valid.
    try:
        from note_schema import inject_schema

        new_content = inject_schema(new_content, file_path)
    except ImportError:
        pass  # don't block append if note_schema unavailable

    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(new_content, encoding="utf-8")

    return {
        "file_path": str(full),
        "bytes_added": len(content),
        "total_bytes": len(new_content),
    }
