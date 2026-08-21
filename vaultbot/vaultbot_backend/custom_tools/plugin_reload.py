"""
Agent-authored tool: plugin_reload
"""

SCHEMA = {
    "name": "plugin_reload",
    "description": (
        "Reload the Obsidian plugin (disable + re-enable) without killing the "
        "backend. Sends a WebSocket message to the plugin which calls "
        "reloadSelf() — Obsidian's plugin API handles the disable/re-enable "
        "cycle. The backend stays running throughout. Use this after editing "
        "main.js or styles.css to pick up changes without the operator having "
        "to manually toggle the plugin in Settings."
    ),
    "parameters": {"properties": {}, "required": [], "type": "object"},
}


def run(args: dict) -> dict:
    """Reload the Obsidian plugin (disable + re-enable) without killing the backend.

    Sends a POST to /reload-plugin which broadcasts a WebSocket message to the
    plugin. The plugin calls reloadSelf() which:
    1. Sets _isReloading = true (prevents onunload from killing backend)
    2. Schedules setTimeout to re-enable the plugin after 500ms
    3. Calls app.plugins.disablePlugin('vaultbot') — onunload skips stopBackend
    4. setTimeout fires: app.plugins.enablePlugin('vaultbot')
    5. New plugin instance connects to existing backend via startBackendIfNeeded()

    The backend stays running throughout. the operator sees a Notice notification.
    Total reload time: ~1-2 seconds (vs ~8s for a full backend restart).
    """
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/reload-plugin",
            method="POST",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        return {
            "status": "reload_requested",
            "message": (
                "Plugin reload signal sent. The plugin will disable and "
                "re-enable itself in ~1s. Backend stays running."
            ),
            "response": result,
        }
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return {
            "status": "error",
            "message": (
                f"Could not reach /reload-plugin endpoint: {e}. Make sure the "
                f"backend is running and the plugin is connected."
            ),
            "error": str(e),
        }
