"""
Agent-authored tool: backend_restart
"""

SCHEMA = {"name": "backend_restart", "description": "Restart the VaultBot backend process and reconnect. Sends a WebSocket message to the Obsidian plugin, which calls restartBackend() — the exact same code path as the GUI restart button. The plugin handles shutdown + respawn. Use this after self-edits that require a backend restart, or to recover from a stale state.", "parameters": {"properties": {}, "required": [], "type": "object"}}

def run(args: dict) -> dict:
    """Restart the VaultBot backend by asking the Obsidian plugin to do it.

    This sends a POST to the backend's /restart endpoint, which broadcasts
    a {"type": "restart"} WebSocket message to all connected clients.
    The plugin's message handler calls this.plugin.restartBackend() —
    the same function the GUI restart button calls. The plugin then:
    1. Calls POST /shutdown (graceful backend shutdown)
    2. Waits 1 second for port release
    3. Spawns a fresh backend process via startBackendIfNeeded()

    No batch scripts, no port polling, no CREATE_BREAKAWAY_FROM_JOB.
    The plugin (a separate process) handles everything.
    """
    import json
    import urllib.request

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
            "response": result,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not reach /restart endpoint: {e}. Make sure the backend is running and the plugin is connected.",
            "error": str(e),
        }