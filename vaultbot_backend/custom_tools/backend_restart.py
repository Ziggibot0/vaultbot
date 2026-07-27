"""
Agent-authored tool: backend_restart
"""

SCHEMA = {"name": "backend_restart", "description": "Restart the VaultBot backend process and reconnect. Sends a WebSocket message to the Obsidian plugin, which calls restartBackend() — the exact same code path as the GUI restart button. The plugin handles shutdown + respawn. Before restarting, this tool automatically caches recent chat history to RESTART_CONTEXT.md so the next session boots with full context — no manual resume needed. Use this after self-edits that require a backend restart, or to recover from a stale state.", "parameters": {"properties": {}, "required": [], "type": "object"}}

def run(args: dict) -> dict:
    """Restart the VaultBot backend by asking the Obsidian plugin to do it.

    Before sending the restart signal, this tool:
    1. Reads the 5 most recent chat logs from 08-Chat/
    2. Writes them to vaultbot_backend/identity/RESTART_CONTEXT.md
    3. The Identity layer picks this up on boot and injects it into the
       system prompt automatically, then deletes the file (one-shot).

    This means after a restart, the agent wakes up already knowing what
    was happening — no manual steps needed.

    The plugin then:
    1. Calls POST /shutdown (graceful backend shutdown)
    2. Waits 1 second for port release
    3. Spawns a fresh backend process via startBackendIfNeeded()
    """
    import json
    import os
    import glob
    import time
    import urllib.request

    # --- Cache context before restart ---------------------------------
    try:
        vault_path = os.getenv("VAULT_PATH", ".")
        chat_dir = os.path.join(vault_path, "08-Chat")
        identity_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "identity"
        )
        restart_ctx_path = os.path.join(identity_dir, "RESTART_CONTEXT.md")

        # Get 5 most recent chat logs
        chat_files = []
        if os.path.isdir(chat_dir):
            for f in glob.glob(os.path.join(chat_dir, "*.md")):
                mtime = os.path.getmtime(f)
                chat_files.append((mtime, f))
            chat_files.sort(key=lambda x: x[0], reverse=True)

        parts = []
        parts.append("# RESTART CONTEXT")
        parts.append(
            f"Restart triggered at: "
            f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        parts.append("")
        parts.append(
            "You were restarted mid-session. The chat history below shows "
            "what was happening. Continue from where you left off — do not "
            "ask Sean to re-explain."
        )
        parts.append("")

        # Include recent chats (last 3000 chars each, most recent first)
        parts.append("## Recent Chat History")
        for mtime, fpath in chat_files[:5]:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if len(content) > 3000:
                    content = "...[truncated]...\n" + content[-3000:]
                parts.append(f"### {os.path.basename(fpath)}")
                parts.append(content)
                parts.append("")
            except Exception as e:
                parts.append(f"### {os.path.basename(fpath)}")
                parts.append(f"Error reading: {e}")
                parts.append("")

        restart_context = "\n".join(parts)

        os.makedirs(identity_dir, exist_ok=True)
        with open(restart_ctx_path, "w", encoding="utf-8") as f:
            f.write(restart_context)

        cached = True
        cache_msg = f"Cached {len(chat_files[:5])} recent chats to RESTART_CONTEXT.md"
    except Exception as e:
        cached = False
        cache_msg = f"Failed to cache context: {e}"

    # --- Send restart signal ------------------------------------------
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/restart",
            method="POST",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        return {
            "status": "restart_requested",
            "message": "Plugin received restart signal. Backend will restart in ~5 seconds. MCP client reconnects automatically.",
            "context_cached": cached,
            "cache_message": cache_msg,
            "response": result,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not reach /restart endpoint: {e}. Make sure the backend is running and the plugin is connected.",
            "context_cached": cached,
            "cache_message": cache_msg,
            "error": str(e),
        }