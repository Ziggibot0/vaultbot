"""
Agent-authored tool: safe_write
"""

SCHEMA = {
    "name": "safe_write",
    "description": "Safely edit backend Python source code with syntax check and auto-rollback. Wraps SelfImprover.safe_write(). Pass file_path (relative to vault root) and content (full file content).",
    "parameters": {
        "properties": {
            "content": {"description": "Full file content to write", "type": "string"},
            "dry_run": {
                "description": "If true, validate only without writing",
                "type": "boolean",
            },
            "file_path": {
                "description": "Path to the file to write, relative to vault root",
                "type": "string",
            },
        },
        "required": ["file_path", "content"],
        "type": "object",
    },
}


def run(args: dict) -> dict:
    import sys, os

    backend_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "vaultbot_stuff",
        "vaultbot_backend",
    )
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from self_improver import SelfImprover

    _si = SelfImprover(session_logger=None)
    result = _si.safe_write(
        file_path=args["file_path"],
        content=args["content"],
        dry_run=args.get("dry_run", False),
    )
    return result
