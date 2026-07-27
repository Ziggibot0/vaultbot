"""
Agent-authored tool: resume_context
"""

SCHEMA = {"name": "resume_context", "description": "Reconstruct working context after a backend restart. Reads GOALS.md, SELF_MODEL.md, and the N most recent chat logs (default 5) to figure out what was being worked on and what to do next. Call this immediately after any restart to auto-resume without Sean having to re-explain.", "parameters": {"properties": {"n_chats": {"description": "Number of recent chat logs to read (default: 5)", "type": "integer"}, "vault_path": {"description": "Path to vault root (default: current directory)", "type": "string"}}, "required": [], "type": "object"}}

def run(args: dict) -> dict:
    """Read GOALS.md + most recent chat logs to reconstruct context after restart."""
    import os, glob
    
    vault_path = args.get("vault_path", ".")
    n_chats = args.get("n_chats", 5)
    
    # 1. Read GOALS.md
    goals_path = os.path.join(vault_path, "vaultbot_backend", "identity", "GOALS.md")
    goals_content = ""
    if os.path.exists(goals_path):
        with open(goals_path, "r", encoding="utf-8") as f:
            goals_content = f.read()
    
    # 2. Read SELF_MODEL.md (truncate to 2000 chars)
    self_model_path = os.path.join(vault_path, "vaultbot_backend", "identity", "SELF_MODEL.md")
    self_model_content = ""
    if os.path.exists(self_model_path):
        with open(self_model_path, "r", encoding="utf-8") as f:
            self_model_content = f.read()[:2000]
    
    # 3. Get N most recently modified chat logs from 08-Chat/
    chat_dir = os.path.join(vault_path, "08-Chat")
    chat_files = []
    if os.path.isdir(chat_dir):
        for f in glob.glob(os.path.join(chat_dir, "*.md")):
            mtime = os.path.getmtime(f)
            chat_files.append((mtime, f))
        chat_files.sort(key=lambda x: x[0], reverse=True)
    
    recent_chats = []
    for mtime, fpath in chat_files[:n_chats]:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            # Truncate very long chats to last 3000 chars (most recent exchange)
            if len(content) > 3000:
                content = "...[truncated]...\n" + content[-3000:]
            recent_chats.append({
                "file": os.path.basename(fpath),
                "content": content
            })
        except Exception as e:
            recent_chats.append({
                "file": os.path.basename(fpath),
                "error": str(e)
            })
    
    return {
        "goals": goals_content,
        "self_model": self_model_content,
        "recent_chats": recent_chats,
        "chat_count_total": len(chat_files)
    }
