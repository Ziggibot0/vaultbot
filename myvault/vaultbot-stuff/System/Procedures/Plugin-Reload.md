---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Reload the Obsidian plugin without killing the backend.
when_to_use: When you've edited the plugin's main.js and need to reload it.
allowed_tools:
  - plugin_reload
summary: The note describes a method to reload the Obsidian plugin without restarting servers, sending WebSocket messages via `custom_tools.plugin_reload` and observing that it toggles off and on automatically
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Plugin-Reload

Reload the Obsidian plugin (disable + re-enable) without killing the backend. Sends a WebSocket message to the plugin to toggle itself off and on.

## Why This Exists

Editing the plugin's `main.js` requires a reload to take effect, but restarting the backend is disruptive and slow. This procedure closes that gap by toggling the plugin off and on via a WebSocket message, so the new code becomes active without a full restart. The tradeoff is that it only reloads the plugin layer — it does not restart the backend process itself.

## Steps

### Step 1: Reload the Obsidian plugin

1. ```python
   from custom_tools.plugin_reload import run as _reload
   result = _reload({})
   print(result)
   ```

### Step 2: Confirm the new code is active

2. [llm: The plugin is reloading. The new main.js code is now active.]

## Related

- [[Backend-Restart]] — the heavier alternative this procedure avoids
- [[Preflight-Safety-Check]] — verify the system is healthy before reloading