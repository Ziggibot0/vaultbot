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

## Steps

### Step 1: Reload the Obsidian plugin

1. ```python
   from custom_tools.plugin_reload import run as _reload
   result = _reload({})
   print(result)
   ```

### Step 2: Confirm the new code is active

2. [llm: The plugin is reloading. The new main.js code is now active.]