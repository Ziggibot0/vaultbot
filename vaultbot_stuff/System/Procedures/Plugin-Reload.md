---
type: procedure
status: active
model_cartridge: small
created: 2026-07-31
description: "Reload the Obsidian plugin without killing the backend."
when_to_use: "When you've edited the plugin's main.js and need to reload it."
allowed_tools: [plugin_reload]
---

# Plugin-Reload

Reload the Obsidian plugin (disable + re-enable) without killing the backend. Sends a WebSocket message to the plugin to toggle itself off and on.

## Steps

1. ```python
   result = plugin_reload()
   print(result)
   ```

2. [llm: The plugin is reloading. The new main.js code is now active.]